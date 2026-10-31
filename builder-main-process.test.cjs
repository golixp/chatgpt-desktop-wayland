"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { findMainProcessChunks, DEFAULT_LIMITS } = require("./builder-main-process.cjs");

function fixture(t, main, files = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "main-process-discovery-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  if (main !== undefined) files["package.json"] = JSON.stringify({ main });
  for (const [name, source] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
    fs.writeFileSync(path.join(root, name), source);
  }
  return root;
}

test("discovers renamed and nested chunks through the manifest without bundler-specific names", (t) => {
  const root = fixture(t, "release/desktop/boot.cjs", {
    "release/desktop/boot.cjs": "require('./chunks/repacked.js'); import('./lazy.mjs')",
    "release/desktop/chunks/repacked.js": "module.exports = {}",
    "release/desktop/lazy.mjs": "export default 1",
    "release/desktop/note.js.map": "{}",
    "release/desktop/node_modules/dep/index.js": "unrelated()",
    "renderer/main.js": "unrelated()",
  });
  assert.deepEqual(findMainProcessChunks(root), {
    buildDir: path.join(root, "release/desktop"),
    chunks: ["boot.cjs", "chunks/repacked.js", "lazy.mjs"],
  });
});

test("supports a manifest entry in the old Vite layout without filename assumptions", (t) => {
  const root = fixture(t, ".vite/build/main-deadbeef.js", {
    ".vite/build/main-deadbeef.js": "require('./future-name.js')",
    ".vite/build/future-name.js": "",
  });
  assert.deepEqual(findMainProcessChunks(root).chunks, ["future-name.js", "main-deadbeef.js"]);
});

test("manifest-less fixtures use a bounded fallback, missing directories return null", (t) => {
  const root = fixture(t, undefined, { ".vite/build/arbitrary.js": "" });
  assert.deepEqual(findMainProcessChunks(root).chunks, ["arbitrary.js"]);
  assert.equal(findMainProcessChunks(path.join(root, "absent")), null);
  assert.equal(findMainProcessChunks(fixture(t, undefined)), null);
});

test("rejects unsafe, absent, malformed and non-JavaScript manifest entries", (t) => {
  for (const main of ["../escape.js", "x/../escape.js", "/absolute.js", "C:\\escape.js", "x\\escape.js", "x\0.js", "missing.js", "", null, 4]) {
    const root = fixture(t, main, { ".vite/build/main.js": "" });
    assert.throws(() => findMainProcessChunks(root), { code: "PATCH_INTEGRITY_FAILURE" });
  }
  for (const main of ["data.json", "directory.js"]) {
    const root = fixture(t, main, { "data.json": "{}" });
    fs.mkdirSync(path.join(root, "directory.js"));
    assert.throws(() => findMainProcessChunks(root), { code: "PATCH_INTEGRITY_FAILURE" });
  }
  const root = fixture(t, "main.js", { "main.js": "" });
  fs.writeFileSync(path.join(root, "package.json"), "{");
  assert.throws(() => findMainProcessChunks(root), /not valid JSON/);
});

test("rejects symlinked manifests, entries, directories and dangling candidates", (t) => {
  const outside = fixture(t, undefined, { "main.js": "" });
  const manifest = fixture(t, undefined, { "manifest.json": '{"main":"main.js"}', "main.js": "" });
  fs.symlinkSync("manifest.json", path.join(manifest, "package.json"));
  assert.throws(() => findMainProcessChunks(manifest), /symbolic link/);
  for (const name of ["main.js", "nested", "dangling.js"]) {
    const root = fixture(t, "app/entry.js", { "app/entry.js": `require('./${name === "nested" ? "nested/main.js" : name}')` });
    fs.symlinkSync(name === "dangling.js" ? "/nonexistent-main-discovery-target" : outside, path.join(root, "app", name));
    assert.throws(() => findMainProcessChunks(root), /symbolic link/);
  }
  const root = fixture(t, "linked/main.js");
  fs.symlinkSync(outside, path.join(root, "linked"));
  assert.throws(() => findMainProcessChunks(root), /symbolic link/);
});

test("bounds discovery depth, file count and bytes", (t) => {
  const root = fixture(t, "app/main.js", {
    "app/main.js": "require('./a/first.js')",
    "app/a/first.js": "require('./b/extra.js')",
    "app/a/b/extra.js": "1234",
  });
  assert.throws(() => findMainProcessChunks(root, { ...DEFAULT_LIMITS, depth: 1 }), /depth exceeds/);
  assert.throws(() => findMainProcessChunks(root, { ...DEFAULT_LIMITS, files: 1 }), /count or size exceeds/);
  assert.throws(() => findMainProcessChunks(root, { ...DEFAULT_LIMITS, bytes: 4 }), /count or size exceeds/);
});


test("unreachable workers, preloads, strings and comments cannot supply a false patch owner", (t) => {
  const root = fixture(t, "app/boot.mjs", {
    "app/boot.mjs": `
      // require('./worker.js'); import './preload.js';
      const example = "require('./worker.js')";
      const worker = new Worker(new URL('./worker.js', import.meta.url));
    `,
    "app/worker.js": "const ONLY_PATCH_OWNER='worker'; require('./'+unknownLoader)",
    "app/preload.js": "const ONLY_PATCH_OWNER='preload'",
  });
  const found = findMainProcessChunks(root);
  assert.deepEqual(found.chunks, ["boot.mjs"]);
  assert.equal(found.chunks.some(name => fs.readFileSync(path.join(found.buildDir, name), "utf8").includes("ONLY_PATCH_OWNER")), false);
});

test("follows literal ESM imports, re-exports, cycles and relative parent edges inside the app", (t) => {
  const root = fixture(t, "app/boot.mjs", {
    "app/boot.mjs": "import '../shared.mjs'; export { value } from './chunks/item.mjs'",
    "shared.mjs": "import './app/boot.mjs'",
    "app/chunks/item.mjs": "export const value = 1",
  });
  assert.deepEqual(findMainProcessChunks(root), {
    buildDir: root,
    chunks: ["app/boot.mjs", "app/chunks/item.mjs", "shared.mjs"],
  });
});

test("resolves renamed Rspack chunk prefixes from a finite data-only runtime map", (t) => {
  const root = fixture(t, "app/renamed-entry.js", {
    "app/renamed-entry.js": `const runtime={};runtime.u=key=>"pieces/new-"+key+"-"+({2:"aaa",8:"bbb"})[key]+".cjs";require("./"+runtime.u(id));`,
    "app/pieces/new-2-aaa.cjs": "module.exports={}",
    "app/pieces/new-8-bbb.cjs": "module.exports={}",
    "app/worker-2-aaa.cjs": "unrelated()",
  });
  assert.deepEqual(findMainProcessChunks(root).chunks, ["pieces/new-2-aaa.cjs", "pieces/new-8-bbb.cjs", "renamed-entry.js"]);
});

test("computed main loaders fail closed when unknown, ambiguous or executable", (t) => {
  const cases = [
    `require('./'+name)`,
    "import(`./${name}.js`)",
    `r.u=id=>doWork(id);require('./'+r.u(id))`,
    `r.u=id=>'piece-'+id+'.js';require('./'+r.u(id))`,
    `r.u=id=>({1:'one.js',1:'two.js'})[id];require('./'+r.u(id))`,
    `r.u=id=>({1:(globalThis.__discoveryExecuted=true)})[id];require('./'+r.u(id))`,
    `r.u=id=>({get 1(){throw Error('must not run')}})[id];require('./'+r.u(id))`,
    `r.u=id=>({1:'a'})[id]+({2:'b'})[id];require('./'+r.u(id))`,
    `r.u=id=>({1:'one.js'})[id];r.u=id=>({2:'two.js'})[id];require('./'+r.u(id))`,
    `r.u=id=>({1:'one.js'})[id]+execute();require('./'+r.u(id))`,
  ];
  for (const source of cases) {
    const root = fixture(t, "app/main.js", { "app/main.js": "const r={};" + source });
    assert.throws(() => findMainProcessChunks(root), { code: "PATCH_INTEGRITY_FAILURE" });
  }
  assert.equal(globalThis.__discoveryExecuted, undefined);
});

test("rejects escaping and missing graph edges while leaving bare runtime modules alone", (t) => {
  for (const source of ["require('../../escape.js')", "require('/absolute.js')", "import('./missing.js')", "require('../../escape.json')"]) {
    const root = fixture(t, "app/main.js", { "app/main.js": source });
    assert.throws(() => findMainProcessChunks(root), { code: "PATCH_INTEGRITY_FAILURE" });
  }
  const root = fixture(t, "app/main.js", { "app/main.js": "require('node:path');require(nativeModule);import(pluginName)" });
  assert.deepEqual(findMainProcessChunks(root).chunks, ["main.js"]);
});


test("computed runtime lookup never crosses lexical bindings or accepts a rebound runtime", (t) => {
  for (const source of [
    `function load(runtime){return require('./'+runtime.u(1))}function unrelated(){let runtime={};runtime.u=id=>({1:'decoy.js'})[id]}`,
    `let runtime={};runtime.u=id=>({1:'decoy.js'})[id];runtime=chooseRuntime();require('./'+runtime.u(1))`,
    `let runtime={};runtime.u=id=>({1:'decoy.js'})[id];({runtime}=chooseRuntime());require('./'+runtime.u(1))`,
    `let runtime={};runtime.u=id=>({1:'decoy.js'})[id];for(runtime of runtimes)require('./'+runtime.u(1))`,
    `let runtime={};runtime.u=id=>({1:'decoy.js'})[id];const cls=class runtime{static load(){require('./'+runtime.u(1))}}`,
    `let runtime={};runtime.u=id=>({1:'decoy.js'})[id];with(other){require('./'+runtime.u(1))}`,
    `const r={};r.u=id=>({1:'decoy.js'})[id];r.u &&= newResolver;require('./'+r.u(1))`,
    `const r={};r.u=id=>({1:'decoy.js'})[id];delete r.u;require('./'+r.u(1))`,
    `const r={};r.u=id=>({1:'decoy.js'})[id];r.u++;require('./'+r.u(1))`,
    `const r={};r.u=id=>({1:'decoy.js'})[id];({u:r.u}=replacement);require('./'+r.u(1))`,
    `function helper(require){require('./decoy.js')}`,
    `const require = customRequire;require('./decoy.js')`,
    `const runtime={};runtime.u=id=>({__proto__:'decoy.js'})[id];require('./'+runtime.u(1))`,
  ]) {
    const root = fixture(t, "app/main.js", { "app/main.js": source, "app/decoy.js": "const falseOwner=true" });
    assert.throws(() => findMainProcessChunks(root), { code: "PATCH_INTEGRITY_FAILURE" });
  }
});

test("nested runtime-loader closures share only their actual ancestor binding", (t) => {
  const root = fixture(t, "app/main.js", {
    "app/main.js": `(()=>{function runtime(id){}runtime.u=id=>({1:'real.js'})[id];runtime.f={load:(id)=>require('./'+runtime.u(id))}})();function other(){const runtime={};runtime.u=id=>({1:'decoy.js'})[id]}`,
    "app/real.js": "const owner=true",
    "app/decoy.js": "const falseOwner=true",
  });
  assert.deepEqual(findMainProcessChunks(root).chunks, ["main.js", "real.js"]);
});
