import { useCallback, useRef, useState } from "react";
import { streamSynthesis } from "../api/client";
import type { SynthesisRequest } from "../types";

export function useSynthesis() {
  const [text, setText] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef(false);

  const synthesize = useCallback(async (params: SynthesisRequest) => {
    setText("");
    setIsStreaming(true);
    abortRef.current = false;

    try {
      for await (const chunk of streamSynthesis(params)) {
        if (abortRef.current) break;
        setText((prev) => prev + chunk);
      }
    } catch (e) {
      setText((prev) => prev + `\n\n[Erreur: ${e}]`);
    } finally {
      setIsStreaming(false);
    }
  }, []);

  const abort = useCallback(() => {
    abortRef.current = true;
  }, []);

  return { text, isStreaming, synthesize, abort };
}
