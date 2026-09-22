import assert from "node:assert/strict";
import test from "node:test";

import { hasOriginalContent, snapshotFingerprint } from "../resource_review_bridge/browser_rules.mjs";

test("snapshot fingerprints detect duplicate carousel captures", () => {
  const snapshot = { url: "https://example.com/post", title: "Post", text: "Visible post content" };
  assert.equal(snapshotFingerprint(snapshot), snapshotFingerprint(snapshot));
  assert.notEqual(snapshotFingerprint(snapshot), snapshotFingerprint({ ...snapshot, text: "Next slide" }));
});

test("social pages require a canonical post path and structural evidence", () => {
  const snapshot = { text: "A sufficiently long visible post body" };
  assert.equal(hasOriginalContent("instagram", "https://www.instagram.com/accounts/login/", snapshot, { mainCount: 1 }), false);
  assert.equal(hasOriginalContent("instagram", "https://www.instagram.com/p/example/", snapshot, { articleCount: 1 }), true);
  assert.equal(hasOriginalContent("threads", "https://www.threads.net/@example/post/abc", snapshot, { mainCount: 1 }), true);
  assert.equal(hasOriginalContent("threads", "https://www.threads.net/login", snapshot, { mainCount: 1 }), false);
});

test("generic public pages still use bounded visible text", () => {
  assert.equal(hasOriginalContent("web", "https://example.com/", { text: "short" }), false);
  assert.equal(hasOriginalContent("web", "https://example.com/", { text: "This is enough visible page content." }), true);
});
