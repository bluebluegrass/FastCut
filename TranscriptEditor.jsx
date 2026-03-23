import { useState, useRef, useEffect, useCallback } from "react";

const REVIEW_EDGE_WINDOW_MS = 700;
const REVIEW_POLL_MS = 40;
const REVIEW_SEEK_GUARD_MS = 160;
const DRAG_SELECTION_THRESHOLD_PX = 5;
const SHORTCUTS = {
  cut: { key: "d", label: "D" },
  restore: { key: "f", label: "F" },
  playPause: { key: "space", label: "Space" },
};
const SHORT_PAUSE_THRESHOLD_MS = 700;
const DERIVED_PAUSE_THRESHOLD_MS = 1400;

function msToTime(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const ss = s % 60;
  const mmm = ms % 1000;
  return `${m}:${String(ss).padStart(2, "0")}.${String(mmm).padStart(3, "0")}`;
}

function mergeSegments(segments) {
  const merged = [];
  const sorted = [...segments].sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms);

  for (const segment of sorted) {
    if (!merged.length || segment.start_ms > merged[merged.length - 1].end_ms) {
      merged.push({ ...segment });
      continue;
    }

    const prev = merged[merged.length - 1];
    prev.end_ms = Math.max(prev.end_ms, segment.end_ms);
    prev.label = prev.label === segment.label ? prev.label : `${prev.label} + ${segment.label}`;
    prev.source = prev.source === segment.source ? prev.source : "mixed";
  }

  return merged;
}

function findSegmentAt(segments, currentMs) {
  return segments.find((segment) => currentMs >= segment.start_ms && currentMs < segment.end_ms) || null;
}

function findNextSegment(segments, currentMs) {
  return segments.find((segment) => segment.end_ms > currentMs) || null;
}

function getDeletedMsBefore(segments, timeMs) {
  let deletedMs = 0;
  for (const segment of segments) {
    if (segment.end_ms <= timeMs) {
      deletedMs += segment.end_ms - segment.start_ms;
      continue;
    }
    if (segment.start_ms < timeMs) {
      deletedMs += timeMs - segment.start_ms;
    }
    break;
  }
  return deletedMs;
}

function getEstimatedReviewMs(segments, timeMs) {
  return Math.max(0, timeMs - getDeletedMsBefore(segments, timeMs));
}

function getPreviewRange(startIndex, currentIndex) {
  if (startIndex == null || currentIndex == null) {
    return null;
  }
  return {
    start: Math.min(startIndex, currentIndex),
    end: Math.max(startIndex, currentIndex),
  };
}

function buildSentenceBlocks(words) {
  const blocks = [];
  let currentBlock = [];

  const pushCurrentBlock = () => {
    if (currentBlock.length) {
      blocks.push(currentBlock);
      currentBlock = [];
    }
  };

  words.forEach((word, index) => {
    const prevWord = words[index - 1];
    const startsOwnBlock =
      word.kind === "pause" ||
      word.kind === "short_pause" ||
      word.kind === "audio_filler";
    const endsPrevBlock =
      prevWord &&
      (
        prevWord.kind === "pause" ||
        prevWord.kind === "short_pause" ||
        prevWord.kind === "audio_filler" ||
        /[。！？!?…]$/.test(prevWord.word) ||
        word.start_ms - prevWord.end_ms >= 650
      );

    if (startsOwnBlock) {
      pushCurrentBlock();
      blocks.push([word]);
      return;
    }

    if (endsPrevBlock) {
      pushCurrentBlock();
    }

    currentBlock.push(word);
  });

  pushCurrentBlock();
  return blocks;
}

function injectDerivedPauseTokens(sourceWords) {
  if (!sourceWords.length || sourceWords.some((word) => word.kind === "pause")) {
    return sourceWords;
  }

  const withPauses = [];

  sourceWords.forEach((word, index) => {
    const prevWord = sourceWords[index - 1];
    if (prevWord) {
      const gapMs = word.start_ms - prevWord.end_ms;
      if (gapMs >= SHORT_PAUSE_THRESHOLD_MS) {
        withPauses.push({
          word: `[pause ${(gapMs / 1000).toFixed(1)}s]`,
          start_ms: prevWord.end_ms,
          end_ms: word.start_ms,
          kind: gapMs >= DERIVED_PAUSE_THRESHOLD_MS ? "pause" : "short_pause",
          is_filler: false,
          deleted: false,
          derived: true,
        });
      }
    }
    withPauses.push(word);
  });

  return withPauses;
}

function normalizeEditorWords(sourceWords) {
  return injectDerivedPauseTokens(sourceWords).map((word, index) => ({
    ...word,
    id: word.id ?? `word-${index}`,
    deleted: word.deleted ?? (word.kind === "audio_filler" ? true : false),
  }));
}

function formatInlineTokenLabel(word) {
  if (word.kind === "pause") {
    const match = word.word.match(/(\d+(?:\.\d+)?)s/i);
    const duration = match ? `${match[1]}s` : "pause";
    return `〈停顿 ${duration}〉`;
  }

  if (word.kind === "short_pause") {
    const match = word.word.match(/(\d+(?:\.\d+)?)s/i);
    const duration = match ? `${match[1]}s` : "";
    return duration ? `〈短停顿 ${duration}〉` : "〈短停顿〉";
  }

  if (word.kind === "audio_filler") {
    const match = word.word.match(/(\d+(?:\.\d+)?)s/i);
    const duration = match ? `${match[1]}s` : "";
    return duration ? `〈思考音 ${duration}〉` : "〈思考音〉";
  }

  return word.word;
}

function getBlockTrailingPunctuation(block, nextBlock) {
  if (!block.length) {
    return "";
  }

  const lastWord = block[block.length - 1];
  if (lastWord.kind === "pause" || lastWord.kind === "short_pause" || lastWord.kind === "audio_filler") {
    return "";
  }

  const lastLabel = formatInlineTokenLabel(lastWord);
  if (/[。！？!?…]$/.test(lastLabel)) {
    return "";
  }

  const nextFirstWord = nextBlock?.[0];
  if (nextFirstWord?.kind === "short_pause" || nextFirstWord?.kind === "audio_filler") {
    return "，";
  }
  if (nextFirstWord?.kind === "pause") {
    return "。";
  }
  if (!nextFirstWord) {
    return "。";
  }

  return "。";
}

export default function TranscriptEditor({ session, draft, videoUrl, previewUrl, previewLoading, mediaType, onDraftChange, onPreview, onExport }) {
  const hydrateSessionKeyRef = useRef(null);
  const transcriptRef = useRef(null);
  const [words, setWords] = useState(() =>
    normalizeEditorWords(draft?.words ?? session.words)
  );
  const [currentMs, setCurrentMs] = useState(0);
  const [searchText, setSearchText] = useState("");
  const [manualStartMs, setManualStartMs] = useState(0);
  const [manualEndMs, setManualEndMs] = useState(Math.min(session.duration_ms, 1000));
  const [manualCuts, setManualCuts] = useState(draft?.manualCuts ?? []);
  const [reviewMode, setReviewMode] = useState(draft?.reviewMode ?? false);
  const [lastSkippedSegment, setLastSkippedSegment] = useState(null);
  const [skipToast, setSkipToast] = useState(null);
  const [selectedReviewSegment, setSelectedReviewSegment] = useState(draft?.selectedReviewSegment ?? null);
  const [selectedWordId, setSelectedWordId] = useState(draft?.selectedWordId ?? null);
  const [advancedReviewOpen, setAdvancedReviewOpen] = useState(false);
  const [advancedToolsOpen, setAdvancedToolsOpen] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isDraggingSelection, setIsDraggingSelection] = useState(false);
  const [dragStartIndex, setDragStartIndex] = useState(null);
  const [dragCurrentIndex, setDragCurrentIndex] = useState(null);
  const [didExceedDragThreshold, setDidExceedDragThreshold] = useState(false);
  const [selectionStartIndex, setSelectionStartIndex] = useState(draft?.selectionStartIndex ?? null);
  const [selectionEndIndex, setSelectionEndIndex] = useState(draft?.selectionEndIndex ?? null);
  const mediaRef = useRef();
  const boundedPlaybackRef = useRef(null);
  const skipGuardRef = useRef({ targetMs: -1, untilMs: 0 });
  const wordsRef = useRef(words);
  const dragStateRef = useRef({
    isDraggingSelection: false,
    dragStartIndex: null,
    dragCurrentIndex: null,
    didExceedDragThreshold: false,
  });
  const dragStartPointRef = useRef({ clientX: 0, clientY: 0 });
  const mouseDownWordRef = useRef(null);
  const dragCleanupRef = useRef({ handleMouseMove: null, handleMouseUp: null });
  const pendingReviewPlayRef = useRef(false);

  const deletedSegments = mergeSegments([
    ...words
      .filter((w) => w.deleted)
      .map((w) => ({
        id: `word-${w.id}`,
        start_ms: w.start_ms,
        end_ms: w.end_ms,
        label: w.word,
        source: w.kind,
      })),
    ...manualCuts.map((cut) => ({
      id: cut.id,
      start_ms: cut.start_ms,
      end_ms: cut.end_ms,
      label: cut.label,
      source: "manual_cut",
    })),
  ]);

  const nextDeletedSegment = findNextSegment(deletedSegments, currentMs);
  const accuratePreviewActive = Boolean(reviewMode && previewUrl);
  const currentEstimatedMs = accuratePreviewActive ? currentMs : getEstimatedReviewMs(deletedSegments, currentMs);
  const dragPreviewRange = getPreviewRange(dragStartIndex, dragCurrentIndex);
  const selectedRange = getPreviewRange(selectionStartIndex, selectionEndIndex);
  const wordIndexById = words.reduce((acc, word, index) => {
    acc[word.id] = index;
    return acc;
  }, {});
  const sentenceBlocks = buildSentenceBlocks(words);
  const deletedCount = words.filter((w) => w.deleted).length + manualCuts.length;
  const fillerCount = words.filter((w) => w.is_filler && !w.deleted).length;
  const pauseCount = words.filter((w) => w.kind === "pause" && !w.deleted).length;
  const audioFillerCount = words.filter((w) => w.kind === "audio_filler" && !w.deleted).length;
  const deletedMs =
    words
      .filter((w) => w.deleted)
      .reduce((sum, w) => sum + (w.end_ms - w.start_ms), 0) +
    manualCuts.reduce((sum, cut) => sum + (cut.end_ms - cut.start_ms), 0);

  const stopBoundedPlayback = useCallback(() => {
    if (boundedPlaybackRef.current?.timeoutId) {
      window.clearTimeout(boundedPlaybackRef.current.timeoutId);
    }
    boundedPlaybackRef.current = null;
  }, []);

  const seekWithGuard = useCallback((targetMs) => {
    const media = mediaRef.current;
    if (!media) return;
    skipGuardRef.current = {
      targetMs,
      untilMs: performance.now() + REVIEW_SEEK_GUARD_MS,
    };
    media.currentTime = targetMs / 1000;
  }, []);

  const playWindow = useCallback((startMs, endMs) => {
    const media = mediaRef.current;
    if (!media) return;

    stopBoundedPlayback();
    const boundedStart = Math.max(0, Math.min(startMs, session.duration_ms));
    const boundedEnd = Math.max(boundedStart, Math.min(endMs, session.duration_ms));
    seekWithGuard(boundedStart);
    media.play();

    boundedPlaybackRef.current = {
      endMs: boundedEnd,
      timeoutId: window.setTimeout(() => {
        media.pause();
        boundedPlaybackRef.current = null;
      }, Math.max(100, boundedEnd - boundedStart)),
    };
  }, [seekWithGuard, session.duration_ms, stopBoundedPlayback]);

  useEffect(() => {
    dragStateRef.current = {
      isDraggingSelection,
      dragStartIndex,
      dragCurrentIndex,
      didExceedDragThreshold,
    };
  }, [didExceedDragThreshold, dragCurrentIndex, dragStartIndex, isDraggingSelection]);

  useEffect(() => {
    wordsRef.current = words;
  }, [words]);

  const clearDragSelection = useCallback(() => {
    setIsDraggingSelection(false);
    setDragStartIndex(null);
    setDragCurrentIndex(null);
    setDidExceedDragThreshold(false);
    mouseDownWordRef.current = null;
    document.body.classList.remove("transcript-dragging");
    dragStateRef.current = {
      isDraggingSelection: false,
      dragStartIndex: null,
      dragCurrentIndex: null,
      didExceedDragThreshold: false,
    };
  }, []);

  const removeDragListeners = useCallback(() => {
    if (dragCleanupRef.current.handleMouseMove) {
      document.removeEventListener("mousemove", dragCleanupRef.current.handleMouseMove);
    }
    if (dragCleanupRef.current.handleMouseUp) {
      document.removeEventListener("mouseup", dragCleanupRef.current.handleMouseUp);
    }
    dragCleanupRef.current = { handleMouseMove: null, handleMouseUp: null };
  }, []);

  const getDragDistance = useCallback((clientX, clientY) => {
    const dx = clientX - dragStartPointRef.current.clientX;
    const dy = clientY - dragStartPointRef.current.clientY;
    return Math.hypot(dx, dy);
  }, []);

  const resolveTokenIndexFromPoint = useCallback((clientX, clientY) => {
    const element = document.elementFromPoint(clientX, clientY);
    const tokenElement = element?.closest?.("[data-token-index]");
    if (!tokenElement) {
      return null;
    }
    const parsedIndex = Number(tokenElement.dataset.tokenIndex);
    return Number.isNaN(parsedIndex) ? null : parsedIndex;
  }, []);

  const buildReviewSegment = useCallback((item) => ({
    id: item.id?.toString?.().startsWith("manual-") ? item.id : `word-${item.id}`,
    start_ms: item.start_ms,
    end_ms: item.end_ms,
    label: item.word || item.label,
    source: item.kind || item.source || "word",
  }), []);

  const setSelectionRange = useCallback((startIndex, endIndex) => {
    const nextRange = getPreviewRange(startIndex, endIndex);
    if (!nextRange) {
      return;
    }
    setSelectionStartIndex(nextRange.start);
    setSelectionEndIndex(nextRange.end);

    const targetWord = wordsRef.current[nextRange.end] ?? wordsRef.current[nextRange.start];
    if (targetWord) {
      setSelectedWordId(targetWord.id);
    }
  }, []);

  const applyDeletedStateRange = useCallback((startIndex, endIndex, nextDeleted) => {
    const previewRange = getPreviewRange(startIndex, endIndex);
    if (!previewRange) {
      return;
    }

    setWords((prev) =>
      prev.map((word, index) => (
        index >= previewRange.start && index <= previewRange.end
          ? { ...word, deleted: nextDeleted }
          : word
      ))
    );

    const targetWord = wordsRef.current[previewRange.end] ?? wordsRef.current[previewRange.start];
    if (targetWord) {
      setSelectedWordId(targetWord.id);
      if (nextDeleted) {
        setSelectedReviewSegment(buildReviewSegment(targetWord));
      } else {
        setSelectedReviewSegment((prev) => (
          prev?.id === `word-${targetWord.id}` ? null : prev
        ));
      }
    }
  }, [buildReviewSegment]);

  const handlePotentialSkip = useCallback((timeMs) => {
    if (!reviewMode || !deletedSegments.length) {
      return false;
    }

    const guard = skipGuardRef.current;
    if (guard.untilMs > performance.now() && Math.abs(timeMs - guard.targetMs) < REVIEW_SEEK_GUARD_MS) {
      return false;
    }

    const skippedSegment = findSegmentAt(deletedSegments, timeMs);
    if (!skippedSegment) {
      return false;
    }

    setLastSkippedSegment(skippedSegment);
    setSkipToast(`Skipped ${((skippedSegment.end_ms - skippedSegment.start_ms) / 1000).toFixed(1)}s`);
    seekWithGuard(skippedSegment.end_ms);
    return true;
  }, [deletedSegments, reviewMode, seekWithGuard]);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media) return;

    const onTime = () => {
      const nextMs = Math.floor(media.currentTime * 1000);
      setCurrentMs(nextMs);
      if (boundedPlaybackRef.current && nextMs >= boundedPlaybackRef.current.endMs) {
        media.pause();
        stopBoundedPlayback();
      }
    };
    const onPlay = () => setIsPlaying(true);
    const onPause = () => {
      setIsPlaying(false);
      stopBoundedPlayback();
    };

    media.addEventListener("timeupdate", onTime);
    media.addEventListener("play", onPlay);
    media.addEventListener("pause", onPause);
    return () => {
      media.removeEventListener("timeupdate", onTime);
      media.removeEventListener("play", onPlay);
      media.removeEventListener("pause", onPause);
    };
  }, [stopBoundedPlayback]);

  useEffect(() => {
    if (hydrateSessionKeyRef.current === session.video_id) {
      return;
    }

    const nextWords = normalizeEditorWords(draft?.words ?? session.words);
    hydrateSessionKeyRef.current = session.video_id;
    setWords(nextWords);
    setManualCuts(draft?.manualCuts ?? []);
    setReviewMode(draft?.reviewMode ?? false);
    setSelectedReviewSegment(draft?.selectedReviewSegment ?? null);
    setSelectedWordId(draft?.selectedWordId ?? null);
    setSelectionStartIndex(draft?.selectionStartIndex ?? null);
    setSelectionEndIndex(draft?.selectionEndIndex ?? null);
    setAdvancedReviewOpen(false);
    setAdvancedToolsOpen(false);
  }, [draft, session.video_id, session.words]);

  useEffect(() => {
    if (!onDraftChange) return;
    onDraftChange({
      words,
      manualCuts,
      reviewMode,
      selectedReviewSegment,
      selectedWordId,
      selectionStartIndex,
      selectionEndIndex,
    });
  }, [manualCuts, onDraftChange, reviewMode, selectedReviewSegment, selectedWordId, selectionEndIndex, selectionStartIndex, words]);

  useEffect(() => {
    if (!reviewMode || !isPlaying || accuratePreviewActive) {
      return undefined;
    }

    const intervalId = window.setInterval(() => {
      const media = mediaRef.current;
      if (!media || media.paused) return;
      handlePotentialSkip(Math.floor(media.currentTime * 1000));
    }, REVIEW_POLL_MS);

    return () => window.clearInterval(intervalId);
  }, [accuratePreviewActive, handlePotentialSkip, isPlaying, reviewMode]);

  useEffect(() => {
    if (!skipToast) return undefined;
    const timeoutId = window.setTimeout(() => setSkipToast(null), 1200);
    return () => window.clearTimeout(timeoutId);
  }, [skipToast]);

  useEffect(() => {
    const media = mediaRef.current;
    if (!media || !accuratePreviewActive || !pendingReviewPlayRef.current) {
      return undefined;
    }

    const startPlayback = () => {
      pendingReviewPlayRef.current = false;
      media.currentTime = 0;
      media.play();
    };

    media.addEventListener("loadedmetadata", startPlayback, { once: true });
    media.addEventListener("canplay", startPlayback, { once: true });
    return () => {
      media.removeEventListener("loadedmetadata", startPlayback);
      media.removeEventListener("canplay", startPlayback);
    };
  }, [accuratePreviewActive, previewUrl]);

  useEffect(() => {
    if (!selectedReviewSegment) return;
    const stillExists = deletedSegments.find((segment) => segment.id === selectedReviewSegment.id);
    if (!stillExists) {
      setSelectedReviewSegment(null);
    }
  }, [deletedSegments, selectedReviewSegment]);

  useEffect(() => () => {
    removeDragListeners();
    clearDragSelection();
  }, [clearDragSelection, removeDragListeners]);

  const setWordDeleted = useCallback((word, nextDeleted) => {
    setWords((prev) =>
      prev.map((w) => (w.id === word.id ? { ...w, deleted: nextDeleted } : w))
    );
    setSelectedWordId(word.id);
    const index = wordIndexById[word.id];
    if (index != null) {
      setSelectionRange(index, index);
    }
    if (nextDeleted) {
      setSelectedReviewSegment(buildReviewSegment(word));
    } else {
      setSelectedReviewSegment((prev) => (prev?.id === `word-${word.id}` ? null : prev));
    }
  }, [buildReviewSegment, setSelectionRange, wordIndexById]);

  const handleTranscriptKeyDown = useCallback((event) => {
    const activeTag = document.activeElement?.tagName;
    if (activeTag === "INPUT" || activeTag === "TEXTAREA" || document.activeElement?.isContentEditable) {
      return;
    }

    const media = mediaRef.current;
    if ((event.key === " " || event.code === "Space" || event.key.toLowerCase() === SHORTCUTS.playPause.key) && media) {
      event.preventDefault();
      if (media.paused) {
        media.play();
      } else {
        media.pause();
      }
      return;
    }

    if (!selectedRange) {
      return;
    }

    if (event.key.toLowerCase() === SHORTCUTS.cut.key && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      applyDeletedStateRange(selectedRange.start, selectedRange.end, true);
      return;
    }

    if (event.key.toLowerCase() === SHORTCUTS.restore.key && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      applyDeletedStateRange(selectedRange.start, selectedRange.end, false);
    }
  }, [applyDeletedStateRange, selectedRange]);

  const markAllFillers = () => {
    setWords((prev) =>
      prev.map((w) => (w.is_filler ? { ...w, deleted: true } : w))
    );
  };

  const clearAll = () => {
    setWords((prev) => prev.map((w) => ({ ...w, deleted: false })));
    setManualCuts([]);
    setSelectedReviewSegment(null);
    setSelectedWordId(null);
    setSelectionStartIndex(null);
    setSelectionEndIndex(null);
  };

  const findAndDelete = () => {
    if (!searchText.trim()) return;
    const lower = searchText.trim().toLowerCase();
    setWords((prev) =>
      prev.map((w) =>
        w.word.toLowerCase().includes(lower) ? { ...w, deleted: true } : w
      )
    );
    setSearchText("");
  };

  const handleExport = () => {
    const deleted = [
      ...words.filter((w) => w.deleted),
      ...manualCuts.map((cut) => ({
        word: cut.label,
        start_ms: cut.start_ms,
        end_ms: cut.end_ms,
        is_filler: false,
        deleted: true,
        kind: "manual_cut",
      })),
    ];
    if (deleted.length === 0) {
      alert("Select something to remove first.");
      return;
    }
    onExport(deleted);
  };

  const clickWord = useCallback((word, shouldAutoplay = true) => {
    const media = mediaRef.current;
    if (!media) return;

    if (word.id != null) {
      setSelectedWordId(word.id);
      const index = wordIndexById[word.id];
      if (index != null) {
        setSelectionRange(index, index);
      }
    }
    if (word.deleted || word.kind === "pause" || word.kind === "short_pause" || word.kind === "audio_filler" || word.source === "manual_cut") {
      setSelectedReviewSegment(buildReviewSegment(word));
    }

    const skippedSegment = reviewMode && !accuratePreviewActive ? findSegmentAt(deletedSegments, word.start_ms) : null;
    const targetMs = accuratePreviewActive
      ? getEstimatedReviewMs(deletedSegments, word.start_ms)
      : skippedSegment
      ? skippedSegment.end_ms
      : word.start_ms;
    seekWithGuard(targetMs);
    if (shouldAutoplay) {
      media.play();
    }
  }, [accuratePreviewActive, buildReviewSegment, deletedSegments, reviewMode, seekWithGuard, setSelectionRange, wordIndexById]);

  const handleTokenMouseDown = useCallback((event, word, index) => {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    removeDragListeners();
    transcriptRef.current?.focus?.({ preventScroll: true });

    dragStartPointRef.current = {
      clientX: event.clientX,
      clientY: event.clientY,
    };
    mouseDownWordRef.current = word;
    setIsDraggingSelection(true);
    setDragStartIndex(index);
    setDragCurrentIndex(index);
    setDidExceedDragThreshold(false);
    dragStateRef.current = {
      isDraggingSelection: true,
      dragStartIndex: index,
      dragCurrentIndex: index,
      didExceedDragThreshold: false,
    };

    const handleMouseMove = (moveEvent) => {
      const nextIndex = resolveTokenIndexFromPoint(moveEvent.clientX, moveEvent.clientY);
      const currentDragState = dragStateRef.current;
      if (!currentDragState.isDraggingSelection) {
        return;
      }

      if (!currentDragState.didExceedDragThreshold) {
        const distance = getDragDistance(moveEvent.clientX, moveEvent.clientY);
        if (distance >= DRAG_SELECTION_THRESHOLD_PX) {
          setDidExceedDragThreshold(true);
          document.body.classList.add("transcript-dragging");
          dragStateRef.current = {
            ...dragStateRef.current,
            didExceedDragThreshold: true,
          };
        }
      }

      if (nextIndex != null) {
        setDragCurrentIndex(nextIndex);
        dragStateRef.current = {
          ...dragStateRef.current,
          dragCurrentIndex: nextIndex,
        };
      }
    };

    const handleMouseUp = () => {
      const currentDragState = dragStateRef.current;
      const clickedWord = mouseDownWordRef.current;
      removeDragListeners();

      if (!currentDragState.didExceedDragThreshold) {
        clearDragSelection();
        if (clickedWord) {
          clickWord(clickedWord, false);
        }
        return;
      }

      setSelectionRange(
        currentDragState.dragStartIndex,
        currentDragState.dragCurrentIndex
      );
      clearDragSelection();
    };

    dragCleanupRef.current = { handleMouseMove, handleMouseUp };
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, [clearDragSelection, clickWord, getDragDistance, removeDragListeners, resolveTokenIndexFromPoint, setSelectionRange]);

  const clampManualRange = (nextStart, nextEnd) => {
    const start = Math.max(0, Math.min(nextStart, session.duration_ms));
    const end = Math.max(0, Math.min(nextEnd, session.duration_ms));
    return start <= end ? [start, end] : [end, start];
  };

  const addManualCut = () => {
    const [start, end] = clampManualRange(manualStartMs, manualEndMs);
    if (end - start < 100) {
      alert("Manual cut range is too short.");
      return;
    }
    const newCut = {
      id: `manual-${start}-${end}-${manualCuts.length}`,
      start_ms: start,
      end_ms: end,
      label: `[manual cut ${msToTime(start)}-${msToTime(end)}]`,
    };
    setManualCuts((prev) => [...prev, newCut]);
    setSelectedReviewSegment(buildReviewSegment(newCut));
  };

  const removeManualCut = (id) => {
    setManualCuts((prev) => prev.filter((cut) => cut.id !== id));
    setSelectedReviewSegment((prev) => (prev?.id === id ? null : prev));
  };

  const setCurrentAsStart = () => {
    const [start, end] = clampManualRange(currentMs, manualEndMs);
    setManualStartMs(start);
    setManualEndMs(end);
  };

  const setCurrentAsEnd = () => {
    const [start, end] = clampManualRange(manualStartMs, currentMs);
    setManualStartMs(start);
    setManualEndMs(end);
  };

  const playReview = () => {
    const media = mediaRef.current;
    if (!media) return;
    media.play();
  };

  const startEditedPreview = async () => {
    const deleted = [
      ...words.filter((w) => w.deleted),
      ...manualCuts.map((cut) => ({
        word: cut.label,
        start_ms: cut.start_ms,
        end_ms: cut.end_ms,
        is_filler: false,
        deleted: true,
        kind: "manual_cut",
      })),
    ];
    if (deleted.length === 0) {
      alert("Select something to remove first.");
      return;
    }

    try {
      await onPreview(deleted);
      pendingReviewPlayRef.current = true;
      setReviewMode(true);
    } catch (error) {
      alert(error.message || "Preview failed");
    }
  };

  const replaySegmentEdge = useCallback((segment) => {
    if (!segment) return;
    playWindow(
      Math.max(0, segment.start_ms - REVIEW_EDGE_WINDOW_MS),
      Math.min(session.duration_ms, segment.end_ms + REVIEW_EDGE_WINDOW_MS)
    );
  }, [playWindow, session.duration_ms]);

  const playBeforeCut = useCallback((segment) => {
    if (!segment) return;
    playWindow(
      Math.max(0, segment.start_ms - REVIEW_EDGE_WINDOW_MS),
      Math.min(session.duration_ms, segment.start_ms + 180)
    );
  }, [playWindow, session.duration_ms]);

  const playAfterCut = useCallback((segment) => {
    if (!segment) return;
    playWindow(
      Math.max(0, segment.end_ms - 180),
      Math.min(session.duration_ms, segment.end_ms + REVIEW_EDGE_WINDOW_MS)
    );
  }, [playWindow, session.duration_ms]);

  return (
    <div className="editor">
      <div className="editor-left">
          {mediaType === "audio" ? (
            <audio
              ref={mediaRef}
              src={accuratePreviewActive ? previewUrl : videoUrl}
              controls
              className="editor-audio"
            />
          ) : (
            <video
              ref={mediaRef}
              src={accuratePreviewActive ? previewUrl : videoUrl}
              controls
              className="editor-video"
            />
        )}

        <div className={`review-panel ${reviewMode ? "review-panel-active" : ""}`}>
          <div className="review-header">
            <span className="review-title">Preview</span>
          </div>
          <div className="review-summary">
            <div className="review-summary-stat">
              <span className="review-summary-value">{deletedCount}</span>
              <span className="review-summary-label">cuts marked</span>
            </div>
            <div className="review-summary-stat">
              <span className="review-summary-value">{(deletedMs / 1000).toFixed(1)}s</span>
              <span className="review-summary-label">time trimmed</span>
            </div>
          </div>
          <div className="review-status">
            {previewLoading
              ? "Preparing your preview…"
              : reviewMode
              ? "Previewing the edited version"
              : "Previewing the original file"}
          </div>
          <div className="review-actions">
            <button
              onClick={startEditedPreview}
              disabled={previewLoading}
            >
              {previewLoading ? "Preparing preview..." : "Play edited cut"}
            </button>
            {reviewMode && (
              <button
                onClick={() => {
                  setReviewMode(false);
                  const media = mediaRef.current;
                  if (media) {
                    media.pause();
                    media.currentTime = 0;
                  }
                }}
              >
                Play original
              </button>
            )}
          </div>
          {skipToast && <div className="review-toast">{skipToast}</div>}
        </div>

        <details
          className="review-panel review-panel-detail review-panel-collapsed"
          open={advancedReviewOpen}
          onToggle={(event) => setAdvancedReviewOpen(event.currentTarget.open)}
        >
          <summary className="review-collapse-summary">Cut details</summary>
          <div className="review-times">
            <span>Original timeline · {msToTime(currentMs)}</span>
            <span>Edited timeline · {msToTime(currentEstimatedMs)}</span>
          </div>
          <div className="review-actions">
            <button onClick={() => replaySegmentEdge(lastSkippedSegment)} disabled={!lastSkippedSegment}>
              Replay last cut
            </button>
          </div>
          <div className="review-next">
            <span className="review-next-label">Next cut in timeline</span>
            {nextDeletedSegment ? (
              <button
                className="review-next-card"
                onClick={() => {
                  setSelectedReviewSegment(nextDeletedSegment);
                  clickWord(nextDeletedSegment, false);
                }}
              >
                <span>{msToTime(nextDeletedSegment.start_ms)} → {msToTime(nextDeletedSegment.end_ms)}</span>
                <strong>{nextDeletedSegment.label}</strong>
              </button>
            ) : (
              <div className="review-next-empty">No more cuts after the current playhead</div>
            )}
          </div>
          <div className="stats stats-compact">
            <div className="stat">
              <span className="stat-val">{words.length}</span>
              <span className="stat-label">Transcript pieces</span>
            </div>
            <div className="stat stat-filler">
              <span className="stat-val">{fillerCount}</span>
              <span className="stat-label">Filler words left</span>
            </div>
            <div className="stat stat-audio-filler">
              <span className="stat-val">{audioFillerCount}</span>
              <span className="stat-label">Thinking sounds left</span>
            </div>
            <div className="stat stat-pause">
              <span className="stat-val">{pauseCount}</span>
              <span className="stat-label">Long pauses left</span>
            </div>
          </div>
          {selectedReviewSegment && (
            <div className="review-detail-card">
              <div className="review-title">Selected cut</div>
              <div className="review-segment-time">
                {msToTime(selectedReviewSegment.start_ms)} → {msToTime(selectedReviewSegment.end_ms)}
              </div>
              <div className="review-times">
                <span>
                  Edited timeline · {msToTime(getEstimatedReviewMs(deletedSegments, selectedReviewSegment.start_ms))} →
                  {" "}
                  {msToTime(getEstimatedReviewMs(deletedSegments, selectedReviewSegment.end_ms))}
                </span>
              </div>
              <div className="review-segment-label">{selectedReviewSegment.label}</div>
              <div className="review-actions">
                <button onClick={() => playBeforeCut(selectedReviewSegment)}>Play before cut</button>
                <button onClick={() => playAfterCut(selectedReviewSegment)}>Play after cut</button>
              </div>
            </div>
          )}
        </details>

        <details
          className="review-panel review-panel-collapsed review-tools-panel"
          open={advancedToolsOpen}
          onToggle={(event) => setAdvancedToolsOpen(event.currentTarget.open)}
        >
          <summary className="review-collapse-summary">Editing tools</summary>
          <div className="toolbar">
            <button className="btn-filler" onClick={markAllFillers}>
              ✨ Mark filler words, thinking sounds, and pauses
            </button>
            <button className="btn-clear" onClick={clearAll}>
              Reset all cuts
            </button>
          </div>

          <div className="search-bar">
            <input
              type="text"
              placeholder="Find words to cut…"
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && findAndDelete()}
            />
            <button onClick={findAndDelete}>Cut matches</button>
          </div>

          <div className="manual-cut-panel">
            <div className="manual-cut-header">Cut by time</div>
            <div className="manual-cut-now">Playhead · {msToTime(currentMs)}</div>
            <div className="manual-cut-actions">
              <button onClick={setCurrentAsStart}>Set start from playhead</button>
              <button onClick={setCurrentAsEnd}>Set end from playhead</button>
            </div>
            <div className="manual-cut-range">
              <label>
                Start
                <input
                  type="range"
                  min="0"
                  max={session.duration_ms}
                  value={manualStartMs}
                  onChange={(e) => {
                    const [start, end] = clampManualRange(Number(e.target.value), manualEndMs);
                    setManualStartMs(start);
                    setManualEndMs(end);
                  }}
                />
                <span>{msToTime(manualStartMs)}</span>
              </label>
              <label>
                End
                <input
                  type="range"
                  min="0"
                  max={session.duration_ms}
                  value={manualEndMs}
                  onChange={(e) => {
                    const [start, end] = clampManualRange(manualStartMs, Number(e.target.value));
                    setManualStartMs(start);
                    setManualEndMs(end);
                  }}
                />
                <span>{msToTime(manualEndMs)}</span>
              </label>
            </div>
            <button className="btn-manual-cut" onClick={addManualCut}>
              Add cut {msToTime(manualStartMs)} - {msToTime(manualEndMs)}
            </button>
            {manualCuts.length > 0 && (
              <div className="manual-cut-list">
                {manualCuts.map((cut) => (
                  <div key={cut.id} className="manual-cut-item">
                    <button
                      className="manual-cut-jump"
                      onClick={() => {
                        setSelectedReviewSegment(buildReviewSegment(cut));
                        clickWord(cut);
                      }}
                    >
                      {msToTime(cut.start_ms)} - {msToTime(cut.end_ms)}
                    </button>
                    <button className="manual-cut-remove" onClick={() => removeManualCut(cut.id)}>
                      Remove
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </details>

        <div className="legend">
          <span className="legend-item">
            <span className="chip chip-normal">word</span> = keep
          </span>
          <span className="legend-item">
            <span className="chip chip-filler">嗯</span> = filler
          </span>
          <span className="legend-item">
            <span className="chip chip-audio-filler">audio</span> = thinking sound
          </span>
          <span className="legend-item">
            <span className="chip chip-pause">pause</span> = pause
          </span>
          <span className="legend-item">
            <span className="chip chip-deleted">cut</span> = removed in export
          </span>
        </div>
      </div>

      <div className="editor-right">
        <div className="transcript-header">
          <span>Transcript</span>
          <div className="transcript-header-actions">
            <details className="shortcuts-popover">
              <summary className="shortcuts-button">Shortcuts</summary>
              <div className="shortcuts-menu">
                <div className="shortcuts-row">
                  <span className="shortcuts-label">Seek</span>
                  <span className="shortcuts-copy">Click a word</span>
                </div>
                <div className="shortcuts-row">
                  <span className="shortcuts-label">Select</span>
                  <span className="shortcuts-copy">Drag across words</span>
                </div>
                <div className="shortcuts-row">
                  <span className="shortcuts-label">Cut</span>
                  <span className="shortcuts-keys">
                    <kbd className="keycap">{SHORTCUTS.cut.label}</kbd>
                  </span>
                </div>
                <div className="shortcuts-row">
                  <span className="shortcuts-label">Restore</span>
                  <span className="shortcuts-keys">
                    <kbd className="keycap">{SHORTCUTS.restore.label}</kbd>
                  </span>
                </div>
                <div className="shortcuts-row">
                  <span className="shortcuts-label">Play / Pause</span>
                  <span className="shortcuts-keys">
                    <kbd className="keycap keycap-wide">{SHORTCUTS.playPause.label}</kbd>
                  </span>
                </div>
              </div>
            </details>
            <button className="btn-export btn-export-inline" onClick={handleExport} disabled={deletedCount === 0}>
              Export final cut
            </button>
          </div>
        </div>
        <div
          ref={transcriptRef}
          className="transcript"
          tabIndex={0}
          onKeyDown={handleTranscriptKeyDown}
        >
          {sentenceBlocks.map((block, blockIndex) => (
            <div
              key={`block-${blockIndex}-${block[0]?.id ?? blockIndex}`}
              className={`transcript-block ${block.some((item) => item.deleted) ? "transcript-block-has-cut" : ""}`}
            >
              {block.map((w) => {
                const index = wordIndexById[w.id];
                const isSelected = selectedWordId === w.id;
                const isDragPreviewed = Boolean(
                  dragPreviewRange && dragPreviewRange.start <= index && index <= dragPreviewRange.end
                );
                const isSelectionPreviewed = Boolean(
                  selectedRange && selectedRange.start <= index && index <= selectedRange.end
                );
                const classes = [
                  "chip",
                  w.kind === "audio_filler"
                    ? "chip-audio-filler"
                    : w.kind === "pause"
                    ? "chip-pause"
                    : w.kind === "short_pause"
                    ? "chip-short-pause"
                    : w.is_filler
                    ? "chip-filler"
                    : "chip-normal",
                  w.deleted ? "chip-deleted" : "",
                  isDragPreviewed ? "chip-drag-preview" : "",
                  isSelectionPreviewed ? "chip-selection-range" : "",
                  isSelected ? "chip-selected" : "",
                ]
                  .filter(Boolean)
                  .join(" ");
                const originalPreviewTitle = `Original ${msToTime(w.start_ms)} → ${msToTime(w.end_ms)}\nPreview ${msToTime(getEstimatedReviewMs(deletedSegments, w.start_ms))} → ${msToTime(getEstimatedReviewMs(deletedSegments, w.end_ms))}`;
                const displayLabel = formatInlineTokenLabel(w);

                return (
                  <span
                    key={w.id}
                    className={classes}
                    title={originalPreviewTitle}
                    data-token-index={index}
                    onMouseDown={(event) => handleTokenMouseDown(event, w, index)}
                    onDragStart={(event) => event.preventDefault()}
                  >
                    <button
                      type="button"
                      className="chip-main-button"
                    >
                      {w.deleted ? <s>{displayLabel}</s> : displayLabel}
                    </button>
                  </span>
                );
              })}
              {getBlockTrailingPunctuation(block, sentenceBlocks[blockIndex + 1]) && (
                <span className="transcript-block-punctuation">
                  {getBlockTrailingPunctuation(block, sentenceBlocks[blockIndex + 1])}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
