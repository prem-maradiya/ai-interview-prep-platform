/** Browser-native voice: Web Speech API recognition (speech -> text) and speechSynthesis (text -> speech).
 * No server cost. Recognition is Chromium/Safari only; callers must feature-detect with `voiceSupport`. */

const Recognition: any = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;

export const voiceSupport = {
  recognition: !!Recognition,
  synthesis: typeof window.speechSynthesis !== "undefined",
};

export type Listener = { stop: () => void };

/** Start dictation. onText receives the full transcript so far (final + interim) for this session. */
export function listen(onText: (text: string) => void, onEnd: () => void, onError: (msg: string) => void): Listener {
  const rec = new Recognition();
  rec.continuous = true;
  rec.interimResults = true;
  rec.lang = navigator.language || "en-US";
  let finalText = "";
  rec.onresult = (e: any) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) finalText += r[0].transcript;
      else interim += r[0].transcript;
    }
    onText((finalText + interim).trim());
  };
  rec.onerror = (e: any) => {
    if (e.error !== "no-speech" && e.error !== "aborted") {
      onError(e.error === "not-allowed" ? "Microphone access was blocked" : `Voice input error: ${e.error}`);
    }
  };
  rec.onend = onEnd;
  rec.start();
  return { stop: () => rec.stop() };
}

/** Speak text aloud, stripping markdown so symbols aren't read out. */
export function speak(text: string): void {
  if (!voiceSupport.synthesis) return;
  window.speechSynthesis.cancel();
  const plain = text.replace(/```[\s\S]*?```/g, " (code) ").replace(/[*_`#>]/g, "").replace(/\[(.*?)\]\(.*?\)/g, "$1");
  const u = new SpeechSynthesisUtterance(plain);
  u.rate = 1.02;
  window.speechSynthesis.speak(u);
}

export function stopSpeaking(): void {
  if (voiceSupport.synthesis) window.speechSynthesis.cancel();
}
