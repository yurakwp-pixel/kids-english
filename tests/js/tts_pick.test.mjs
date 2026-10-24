import { test } from "node:test";
import assert from "node:assert/strict";
import { pickVoice } from "../../web/js/tts.js";

test("pickVoice prefers en-US Online Natural Ana", () => {
  const v = pickVoice([{ name: "Microsoft David - English (United States)", lang: "en-US" },
    { name: "Microsoft Ana Online (Natural) - English (United States)", lang: "en-US" },
    { name: "Microsoft Aria Online (Natural) - English (United States)", lang: "en-US" }]);
  assert.match(v.name, /Ana Online/);
});

test("pickVoice returns null without English", () => assert.equal(pickVoice([{ name: "x", lang: "ko-KR" }]), null));

test("pickVoice takes another en-US Online Natural voice when Ana is missing", () => {
  const v = pickVoice([{ name: "Microsoft David - English (United States)", lang: "en-US" },
    { name: "Microsoft Aria Online (Natural) - English (United States)", lang: "en-US" }]);
  assert.match(v.name, /Aria Online/);
});

test("pickVoice prefers any en-US voice over other English", () => {
  const v = pickVoice([{ name: "Microsoft Libby Online (Natural) - English (United Kingdom)", lang: "en-GB" },
    { name: "Google US English", lang: "en-US" }]);
  assert.equal(v.name, "Google US English");
});

test("pickVoice falls back to any en-* voice, accepting en_US style tags", () => {
  assert.equal(pickVoice([{ name: "k", lang: "ko-KR" }, { name: "gb", lang: "en-GB" }]).name, "gb");
  assert.equal(pickVoice([{ name: "android", lang: "en_US" }, { name: "gb", lang: "en-GB" }]).name, "android");
});

test("pickVoice does not mistake a longer name for Ana", () => {
  const v = pickVoice([{ name: "Microsoft Anabel Online (Natural) - English (United States)", lang: "en-US" },
    { name: "Microsoft Ana Online (Natural) - English (United States)", lang: "en-US" }]);
  assert.match(v.name, /Ana Online/);
});

test("pickVoice handles empty or missing lists", () => {
  assert.equal(pickVoice([]), null);
  assert.equal(pickVoice(undefined), null);
});
