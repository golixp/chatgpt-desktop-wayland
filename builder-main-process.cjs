"use strict";

// Acorn 8.15.0 is vendored unchanged from the official npm release. Parse source
// as data only: never load the application or evaluate its chunk-name function.
const fs = require("node:fs");
const path = require("node:path");
const acorn = require("./builder-acorn.cjs");
const DEFAULT_LIMITS = Object.freeze({ files: 1024, depth: 32, bytes: 128 * 1024 * 1024 });
const JAVASCRIPT = /\.(?:c|m)?js$/i;

function integrity(message) {
  const error = new Error(`Main-process discovery: ${message}`);
  error.code = "PATCH_INTEGRITY_FAILURE";
  throw error;
}

function checkedPath(root, relative) {
  if (typeof relative !== "string" || relative.length === 0 || relative.includes("\0") ||
      relative.includes("\\") || path.isAbsolute(relative) || /^[A-Za-z]:/.test(relative) ||
      relative.split("/").includes("..")) integrity(`unsafe application path ${JSON.stringify(relative)}`);
  const target = path.resolve(root, relative);
  if (target !== root && !target.startsWith(root + path.sep)) integrity("application path escapes its root");
  let current = root;
  for (const segment of path.relative(root, target).split(path.sep).filter(Boolean)) {
    current = path.join(current, segment);
    const stat = fs.lstatSync(current, { throwIfNoEntry: false });
    if (stat == null) return null;
    if (stat.isSymbolicLink()) integrity(`symbolic link at ${current}`);
  }
  return target;
}

function walkAst(root, visit) {
  const queue = [root];
  while (queue.length) {
    const node = queue.pop();
    if (node == null || typeof node.type !== "string") continue;
    visit(node);
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) {
        for (const child of value) if (child?.type) queue.push(child);
      } else if (value?.type) queue.push(value);
    }
  }
}

function memberName(node) {
  return node?.type === "MemberExpression"
    ? node.computed ? node.property.type === "Literal" ? node.property.value : null : node.property.name
    : null;
}

function staticString(node) {
  if (node?.type === "Literal" && typeof node.value === "string") return node.value;
  if (node?.type === "TemplateLiteral" && node.expressions.length === 0) return node.quasis[0].value.cooked;
  if (node?.type === "BinaryExpression" && node.operator === "+") {
    const left = staticString(node.left), right = staticString(node.right);
    if (left != null && right != null) return left + right;
  }
  return null;
}

function relativePrefix(node) {
  const value = staticString(node);
  if (value != null) return value;
  if (node?.type === "BinaryExpression" && node.operator === "+") return relativePrefix(node.left);
  if (node?.type === "TemplateLiteral") return node.quasis[0].value.cooked;
  return null;
}

function dataTable(node) {
  if (node?.type !== "ObjectExpression") integrity("chunk lookup is not a finite object table");
  const table = new Map();
  for (const property of node.properties) {
    if (property.type !== "Property" || property.kind !== "init" || property.computed || property.method || property.shorthand) {
      integrity("chunk table contains executable or computed properties");
    }
    const key = property.key.type === "Identifier" ? property.key.name : property.key.value;
    if (key === "__proto__") integrity("prototype setters are not chunk table entries");
    const value = property.value;
    if (!["string", "number"].includes(typeof key) || !["string", "number"].includes(typeof value.value) || value.type !== "Literal") {
      integrity("chunk table keys and values must be literal strings or numbers");
    }
    if (table.has(String(key))) integrity("duplicate chunk identifier");
    table.set(String(key), { key, value: value.value });
  }
  if (table.size === 0) integrity("chunk table is empty");
  if (table.size > DEFAULT_LIMITS.files) integrity("chunk table exceeds the module limit");
  return table;
}

function chunkPaths(resolver, prefix) {
  if (!["ArrowFunctionExpression", "FunctionExpression"].includes(resolver?.type) ||
      resolver.params.length !== 1 || resolver.params[0].type !== "Identifier" || resolver.async || resolver.generator) {
    integrity("unsupported computed chunk resolver");
  }
  const parameter = resolver.params[0].name;
  let body = resolver.body;
  if (body.type === "BlockStatement") {
    if (body.body.length !== 1 || body.body[0].type !== "ReturnStatement") integrity("chunk resolver is not data-only");
    body = body.body[0].argument;
  }
  const tables = [];
  walkAst(body, node => {
    if (node.type === "MemberExpression" && node.computed && node.object.type === "ObjectExpression" &&
        node.property.type === "Identifier" && node.property.name === parameter) tables.push(dataTable(node.object));
  });
  if (tables.length === 0) integrity("computed chunk resolver has no finite lookup table");
  const identifiers = [...tables[0].keys()].sort();
  for (const table of tables) {
    if (JSON.stringify([...table.keys()].sort()) !== JSON.stringify(identifiers)) integrity("chunk lookup tables have different domains");
  }
  function evaluate(node, input) {
    if (node?.type === "Literal" && ["string", "number"].includes(typeof node.value)) return node.value;
    if (node?.type === "Identifier" && node.name === parameter) return input;
    if (node?.type === "BinaryExpression" && node.operator === "+") return evaluate(node.left, input) + evaluate(node.right, input);
    if (node?.type === "TemplateLiteral") {
      return node.quasis.map((part, index) => part.value.cooked + (index < node.expressions.length ? evaluate(node.expressions[index], input) : "")).join("");
    }
    if (node?.type === "MemberExpression" && node.computed && node.object.type === "ObjectExpression" &&
        node.property.type === "Identifier" && node.property.name === parameter) {
      const entry = dataTable(node.object).get(String(input));
      if (entry == null) integrity("missing chunk table key");
      return entry.value;
    }
    integrity(`unsupported executable chunk-resolver expression ${node?.type}`);
  }
  return identifiers.map(key => {
    const value = evaluate(body, tables[0].get(key).key);
    if (typeof value !== "string") integrity("chunk resolver did not produce a filename");
    return prefix + value;
  });
}

// Resolve only lexical names needed by the loader contract. This is not a
// JavaScript interpreter: shadowed require(), rebound runtimes, or ambiguous
// assignments are rejected instead of borrowing another scope's chunk table.
function lexicalBindings(ast) {
  const nodeScopes = new WeakMap();
  function scope(parent, isFunction = false) {
    const value = { parent, isFunction, bindings: new Map() };
    return value;
  }
  const global = scope(null, true);
  function bind(pattern, owner, initialized = false) {
    if (pattern == null) return;
    if (pattern.type === "Identifier") {
      let binding = owner.bindings.get(pattern.name);
      if (!binding) owner.bindings.set(pattern.name, binding = { name: pattern.name, initializations: 0, writes: 0 });
      if (initialized) binding.initializations += 1;
    } else if (pattern.type === "RestElement") bind(pattern.argument, owner, initialized);
    else if (pattern.type === "AssignmentPattern") bind(pattern.left, owner, initialized);
    else if (pattern.type === "ArrayPattern") for (const item of pattern.elements) bind(item, owner, initialized);
    else if (pattern.type === "ObjectPattern") for (const item of pattern.properties) bind(item.type === "RestElement" ? item.argument : item.value, owner, initialized);
  }
  function children(node, owner) {
    for (const value of Object.values(node)) {
      if (Array.isArray(value)) for (const child of value) { if (child?.type) visit(child, owner); }
      else if (value?.type) visit(value, owner);
    }
  }
  function visit(node, owner) {
    nodeScopes.set(node, owner);
    if (["FunctionDeclaration", "FunctionExpression", "ArrowFunctionExpression"].includes(node.type)) {
      if (node.type === "FunctionDeclaration") bind(node.id, owner, true);
      const inner = scope(owner, true);
      if (node.type === "FunctionExpression") bind(node.id, inner, true);
      for (const param of node.params) bind(param, inner, true);
      for (const param of node.params) visit(param, inner);
      visit(node.body, inner);
      return;
    }
    if (["ClassDeclaration", "ClassExpression"].includes(node.type)) {
      if (node.type === "ClassDeclaration") bind(node.id, owner, true);
      owner = scope(owner);
      bind(node.id, owner, true);
      nodeScopes.set(node, owner);
    }
    if (["BlockStatement", "StaticBlock", "CatchClause", "ForStatement", "ForInStatement", "ForOfStatement", "SwitchStatement", "WithStatement"].includes(node.type)) {
      owner = scope(owner, node.type === "StaticBlock");
      owner.dynamic = node.type === "WithStatement";
      nodeScopes.set(node, owner);
      if (node.type === "CatchClause") bind(node.param, owner, true);
    }
    if (node.type === "VariableDeclaration") {
      let target = owner;
      if (node.kind === "var") while (!target.isFunction) target = target.parent;
      for (const declaration of node.declarations) bind(declaration.id, target, declaration.init != null);
    }
    if (node.type === "ImportDeclaration") for (const item of node.specifiers) bind(item.local, owner, true);
    children(node, owner);
  }
  visit(ast, global);
  function resolve(node, name) {
    for (let owner = nodeScopes.get(node); owner; owner = owner.parent) {
      if (owner.dynamic) return { name, ambiguous: true };
      if (owner.bindings.has(name)) return owner.bindings.get(name);
    }
    return null;
  }
  walkAst(ast, node => {
    const target = node.type === "AssignmentExpression" ? node.left : node.type === "UpdateExpression" ? node.argument : null;
    function mark(pattern) {
      if (pattern?.type === "Identifier") {
        const binding = resolve(node, pattern.name);
        if (binding) binding.writes += 1;
      } else if (pattern?.type === "ArrayPattern") for (const item of pattern.elements) mark(item);
      else if (pattern?.type === "ObjectPattern") for (const item of pattern.properties) mark(item.value ?? item.argument);
      else if (pattern?.type === "AssignmentPattern") mark(pattern.left);
      else if (pattern?.type === "RestElement") mark(pattern.argument);
    }
    mark(target);
    if (["ForInStatement", "ForOfStatement"].includes(node.type)) {
      if (node.left.type === "VariableDeclaration") for (const item of node.left.declarations) mark(item.id);
      else mark(node.left);
    }
  });
  return { resolve };
}

function moduleDependencies(source, filename) {
  let ast;
  try {
    ast = acorn.parse(source, { ecmaVersion: "latest", sourceType: "script", allowReturnOutsideFunction: true, allowAwaitOutsideFunction: true });
  } catch {
    try { ast = acorn.parse(source, { ecmaVersion: "latest", sourceType: "module", allowReturnOutsideFunction: true }); }
    catch (error) { integrity(`cannot parse ${filename}: ${error.message}`); }
  }
  const bindings = lexicalBindings(ast);
  const dependencies = new Set(), loaders = [], assignments = new Map(), mutations = new Set();
  function request(argument, owner, isRequire = false) {
    const localPrefix = relativePrefix(argument);
    if (isRequire && localPrefix != null && (localPrefix.startsWith(".") || path.isAbsolute(localPrefix)) && bindings.resolve(owner, "require")) {
      integrity(`shadowed require cannot establish a main-module edge in ${filename}`);
    }
    const literal = staticString(argument);
    if (literal != null) {
      if (literal.startsWith(".") || path.isAbsolute(literal)) dependencies.add(literal);
      return;
    }
    const prefix = relativePrefix(argument);
    // Dynamic native/plugin module paths are outside the bootstrap chunk graph.
    // A visibly relative computed load must be resolved, never silently ignored.
    if (prefix == null || (!prefix.startsWith(".") && !path.isAbsolute(prefix))) return;
    const call = argument.type === "BinaryExpression" && argument.operator === "+" && staticString(argument.left) != null
      ? argument.right : null;
    if (call?.type !== "CallExpression" || memberName(call.callee) !== "u" ||
        call.callee.object.type !== "Identifier" || call.arguments.length !== 1) {
      integrity(`unsupported computed local module load in ${filename}`);
    }
    const runtime = bindings.resolve(owner, call.callee.object.name);
    if (runtime == null || runtime.ambiguous || runtime.writes > 0 || runtime.initializations > 1) integrity(`unbound or rebound chunk runtime in ${filename}`);
    loaders.push({ runtime, prefix: staticString(argument.left) });
  }
  function markResolverMutation(target, owner) {
    if (target?.type === "ChainExpression") target = target.expression;
    if (memberName(target) === "u" && target.object.type === "Identifier") {
      const binding = bindings.resolve(owner, target.object.name);
      if (binding) mutations.add(binding);
    } else if (target?.type === "ArrayPattern") for (const item of target.elements) markResolverMutation(item, owner);
    else if (target?.type === "ObjectPattern") for (const item of target.properties) markResolverMutation(item.value ?? item.argument, owner);
    else if (target?.type === "AssignmentPattern") markResolverMutation(target.left, owner);
    else if (target?.type === "RestElement") markResolverMutation(target.argument, owner);
  }
  walkAst(ast, node => {
    if (node.type === "UpdateExpression" || (node.type === "UnaryExpression" && node.operator === "delete")) markResolverMutation(node.argument, node);
    if (["ForInStatement", "ForOfStatement"].includes(node.type)) markResolverMutation(node.left, node);
    if (node.type === "AssignmentExpression" && (node.operator !== "=" || node.left.type !== "MemberExpression")) markResolverMutation(node.left, node);
    if (node.type === "AssignmentExpression" && node.operator === "=" && memberName(node.left) === "u" && node.left.object.type === "Identifier") {
      const binding = bindings.resolve(node, node.left.object.name);
      if (binding != null) assignments.set(binding, [...(assignments.get(binding) ?? []), node.right]);
    }
    if (node.type === "CallExpression" && node.callee.type === "Identifier" && node.callee.name === "require" && node.arguments.length === 1) request(node.arguments[0], node, true);
    if (node.type === "ImportExpression") request(node.source, node);
    if (["ImportDeclaration", "ExportNamedDeclaration", "ExportAllDeclaration"].includes(node.type) && node.source != null) request(node.source, node);
  });
  for (const { runtime, prefix } of loaders) {
    if (mutations.has(runtime)) integrity(`mutated chunk resolver ${runtime.name}.u in ${filename}`);
    const matches = assignments.get(runtime) ?? [];
    if (matches.length !== 1) integrity(`expected one computed resolver for ${runtime.name}.u in ${filename}`);
    for (const dependency of chunkPaths(matches[0], prefix)) dependencies.add(dependency);
  }
  return [...dependencies];
}

function findMainProcessChunks(extractedDir, limits = DEFAULT_LIMITS) {
  const root = path.resolve(extractedDir);
  if (!fs.existsSync(root)) return null;
  if (fs.lstatSync(root).isSymbolicLink() || !fs.statSync(root).isDirectory()) integrity("application root must be a real directory");
  for (const name of ["files", "depth", "bytes"]) {
    if (!Number.isSafeInteger(limits[name]) || limits[name] < 1) integrity(`invalid ${name} limit`);
  }
  const manifestPath = checkedPath(root, "package.json");
  if (manifestPath == null) {
    // Upstream's small unit fixtures predate manifests. Production preflight
    // separately requires package.json; this fallback never drives a real build.
    const buildDir = checkedPath(root, ".vite/build");
    if (buildDir == null) return null;
    const chunks = fs.readdirSync(buildDir).filter(name => JAVASCRIPT.test(name)).sort();
    let bytes = 0;
    for (const name of chunks) {
      const file = checkedPath(root, path.relative(root, path.join(buildDir, name)));
      if (!file || !fs.statSync(file).isFile()) integrity("fixture candidate is not a regular file");
      bytes += fs.statSync(file).size;
    }
    if (chunks.length > limits.files || bytes > limits.bytes) integrity("fixture candidate count or size exceeds the limit");
    return chunks.length === 0 ? null : { buildDir, chunks };
  }
  if (!fs.statSync(manifestPath).isFile() || fs.statSync(manifestPath).size > 1024 * 1024) integrity("package.json must be a regular file smaller than 1 MiB");
  let manifest;
  try { manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8")); }
  catch { integrity("package.json is not valid JSON"); }
  const entry = checkedPath(root, manifest?.main);
  if (entry == null || !fs.statSync(entry).isFile() || !JAVASCRIPT.test(entry)) integrity("package.json main must name an existing JavaScript file");
  const files = new Set();
  let bytes = 0;
  function resolveDependency(from, specifier) {
    if (specifier.includes("\\") || specifier.includes("\0") || path.isAbsolute(specifier)) integrity("unsafe module dependency path");
    const target = path.resolve(path.dirname(from), specifier);
    const relative = path.relative(root, target);
    const file = checkedPath(root, relative);
    // Native/data dependencies do not own JavaScript patch contracts.
    if (/\.(?:json|node|wasm)$/.test(target)) return null;
    if (file != null && fs.statSync(file).isFile()) {
      if (!JAVASCRIPT.test(file)) integrity("unsupported extensionless JavaScript dependency");
      return file;
    }
    const candidates = [target + ".js", target + ".cjs", target + ".mjs", path.join(target, "index.js")]
      .map(value => checkedPath(root, path.relative(root, value)))
      .filter(value => value != null && fs.statSync(value).isFile());
    if (candidates.length !== 1) integrity(`missing or ambiguous local JavaScript dependency ${specifier} from ${path.relative(root, from)}`);
    return candidates[0];
  }
  function visit(file, depth) {
    if (files.has(file)) return;
    if (depth > limits.depth) integrity("module graph depth exceeds the limit");
    files.add(file);
    bytes += fs.statSync(file).size;
    if (files.size > limits.files || bytes > limits.bytes) integrity("module graph count or size exceeds the limit");
    const source = fs.readFileSync(file, "utf8");
    for (const specifier of moduleDependencies(source, path.relative(root, file))) {
      const dependency = resolveDependency(file, specifier);
      if (dependency != null) visit(dependency, depth + 1);
    }
  }
  visit(entry, 0);
  const entryDir = path.dirname(entry);
  const buildDir = [...files].every(file => file.startsWith(entryDir + path.sep)) ? entryDir : root;
  return { buildDir, chunks: [...files].map(file => path.relative(buildDir, file)).sort() };
}

module.exports = { DEFAULT_LIMITS, findMainProcessChunks, moduleDependencies };
