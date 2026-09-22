import { createHash } from "node:crypto";

export function snapshotFingerprint(snapshot, screenshotBuffer = null) {
  const hash = createHash("sha256");
  if (screenshotBuffer?.length) {
    hash.update(screenshotBuffer);
  } else {
    hash.update(JSON.stringify([
      snapshot?.url || "",
      snapshot?.title || "",
      snapshot?.text || "",
    ]));
  }
  return hash.digest("hex");
}

export function hasOriginalContent(platform, finalUrl, snapshot, signals = {}) {
  if ((snapshot?.text || "").trim().length < 20) return false;
  if (platform === "web") return true;

  let parsed;
  try {
    parsed = new URL(finalUrl);
  } catch {
    return false;
  }

  const structuralEvidence = (signals.mainCount || 0) > 0 ||
    (signals.articleCount || 0) > 0 ||
    (signals.ogDescriptionLength || 0) >= 20;
  if (!structuralEvidence) return false;

  if (platform === "instagram") {
    return /(^|\.)instagram\.com$/i.test(parsed.hostname) &&
      /^\/(p|reel|tv)\/[^/]+\/?/i.test(parsed.pathname);
  }
  if (platform === "threads") {
    return /(^|\.)threads\.(net|com)$/i.test(parsed.hostname) &&
      (/^\/@[^/]+\/post\/[^/]+\/?/i.test(parsed.pathname) || /^\/t\/[^/]+\/?/i.test(parsed.pathname));
  }
  return false;
}
