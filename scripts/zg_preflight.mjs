#!/usr/bin/env node
/**
 * Fail-closed, read-only preflight for npm run zg:index.
 *
 * Production zg access uses only the public package entrypoint.
 * Manifest and authorization files are read with Node fs because info()
 * does not expose embeddingRuntime.
 * Never prints a manifest, API keys, endpoints, or stored paths.
 */
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createZvecGrep } from "@zvec/zvec-grep";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(SCRIPT_DIR, "..");

export const ZG_DIR = ".zvec-grep";
export const MANIFEST_FILE = "manifest.json";
export const AUTHORIZATION_FILE = "authorization.json";

export const APPROVED_MANIFEST_VERSION = 1;
export const APPROVED_INDEX_VERSION = 1;
export const APPROVED_INDEX_POLICY = "enabled";
export const APPROVED_PROVIDER = "local";
export const APPROVED_MODEL = "potion-code-16m-v2";
export const APPROVED_DIMENSION = 256;
export const APPROVED_METRIC = "cosine";
export const APPROVED_IGNORE_FILES = Object.freeze([".repomixignore"]);
export const REQUIRED_REPOMIXIGNORE_EXCLUSIONS = Object.freeze([
  ".env",
  ".env.*",
  ".zvec-grep/",
  "uploads/**",
  "instance/**",
  "private_imports/**",
  "reports/generated/**",
  "tests/tmp/**",
  "tests/.tmp/**",
  "references/90_archivo_no_usar/**",
]);
export const APPROVED_INSENSITIVE_GLOBS = Object.freeze([
  "!**/*.png",
  "!**/*.jpg",
  "!**/*.jpeg",
  "!**/*.gif",
  "!**/*.webp",
  "!**/*.tif",
  "!**/*.tiff",
  "!**/*.vips",
]);
export const APPROVED_RUNTIME_DEVICES = Object.freeze(["auto", "cpu"]);

const MANIFEST_KEYS = Object.freeze([
  "manifestVersion",
  "id",
  "name",
  "path",
  "rootPaths",
  "indexPolicy",
  "embedding",
  "indexVersion",
  "createdTime",
  "updatedTime",
  "embeddingRuntime",
]);
const EMBEDDING_KEYS = Object.freeze(["provider", "model", "dimension", "metric"]);
const RUNTIME_KEYS = Object.freeze(["apiKey", "endpoint", "device"]);
const ROOT_PATH_KEYS = Object.freeze([
  "absolutePath",
  "recursive",
  "include",
  "exclude",
  "globs",
  "insensitiveGlobs",
  "fileTypes",
  "excludedFileTypes",
  "hidden",
  "noIgnore",
  "ignoreFiles",
  "maxDepth",
  "maxFileSizeBytes",
  "follow",
]);
const OPERATOR_ACTIONS =
  "Supported explicit operator actions: --rebuild, --reset-paths, --drop. This preflight is read-only and will not run those actions.";

export class ZgPreflightError extends Error {
  constructor(reason) {
    super(reason);
    this.name = "ZgPreflightError";
    this.reason = reason;
  }
}

function fail(reason) {
  throw new ZgPreflightError(reason);
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function extraKeys(value, allowed) {
  return Object.keys(value).filter((key) => !allowed.includes(key));
}

function sameStringList(actual, expected) {
  return (
    Array.isArray(actual) &&
    actual.length === expected.length &&
    actual.every((item, index) => item === expected[index])
  );
}

function emptyOrAbsent(value) {
  return value === undefined || (Array.isArray(value) && value.length === 0);
}

function absentOrFalse(value) {
  return value === undefined || value === false;
}

function readJsonObject(path) {
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    fail("manifest is missing or unreadable.");
  }
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail("manifest is corrupt and cannot be parsed.");
  }
  if (!isRecord(value)) {
    fail("manifest schema is unknown.");
  }
  return value;
}

function assertApprovedEmbedding(embedding) {
  if (embedding === null || embedding === undefined) {
    fail("manifest embedding schema is missing.");
  }
  if (!isRecord(embedding) || extraKeys(embedding, EMBEDDING_KEYS).length > 0) {
    fail("manifest embedding schema is unknown.");
  }
  if (embedding.provider !== APPROVED_PROVIDER) {
    fail("stored provider/runtime is not the approved local Potion Model2Vec backend.");
  }
  if (embedding.model !== APPROVED_MODEL) {
    fail("stored embedding model is not local/potion-code-16m-v2.");
  }
  if (embedding.dimension !== APPROVED_DIMENSION) {
    fail("stored embedding dimensions are not the approved Potion Model2Vec schema.");
  }
  if (embedding.metric !== APPROVED_METRIC) {
    fail("stored embedding metric is not the approved Potion Model2Vec schema.");
  }
}

function assertApprovedRuntime(runtime) {
  if (!isRecord(runtime) || extraKeys(runtime, RUNTIME_KEYS).length > 0) {
    fail("manifest embeddingRuntime schema is unknown.");
  }
  if (Object.hasOwn(runtime, "apiKey")) {
    fail("stored embedding runtime contains persisted credential state.");
  }
  if (Object.hasOwn(runtime, "endpoint")) {
    fail("stored embedding runtime contains a remote endpoint.");
  }
  if (
    runtime.device !== undefined &&
    !APPROVED_RUNTIME_DEVICES.includes(runtime.device)
  ) {
    fail("stored embedding runtime device is not compatible with local Potion Model2Vec.");
  }
}

function assertApprovedRootPath(rootPath, workspaceRoot) {
  if (!isRecord(rootPath) || extraKeys(rootPath, ROOT_PATH_KEYS).length > 0) {
    fail("stored file-selection schema is unknown.");
  }
  if (typeof rootPath.absolutePath !== "string" || rootPath.absolutePath.length === 0) {
    fail("stored file-selection root is missing.");
  }
  if (resolve(rootPath.absolutePath) !== resolve(workspaceRoot)) {
    fail("stored file-selection root does not match this workspace.");
  }
  if (rootPath.recursive !== true) {
    fail("stored file-selection policy is incompatible with the approved recursive root.");
  }
  if (!emptyOrAbsent(rootPath.include) || !emptyOrAbsent(rootPath.exclude)) {
    fail("stored include/exclude path policy is incompatible with the approved policy.");
  }
  if (!emptyOrAbsent(rootPath.globs)) {
    fail("stored glob path policy is incompatible with the approved policy.");
  }
  if (!sameStringList(rootPath.insensitiveGlobs, APPROVED_INSENSITIVE_GLOBS)) {
    fail("stored iglob path policy is incompatible with the approved image exclusions.");
  }
  if (!emptyOrAbsent(rootPath.fileTypes) || !emptyOrAbsent(rootPath.excludedFileTypes)) {
    fail("stored file-type filters are incompatible with the approved policy.");
  }
  if (!absentOrFalse(rootPath.hidden)) {
    fail("stored path policy enables hidden paths.");
  }
  if (!absentOrFalse(rootPath.noIgnore)) {
    fail("stored path policy disables ignore files.");
  }
  if (!sameStringList(rootPath.ignoreFiles, APPROVED_IGNORE_FILES)) {
    fail("stored ignore-file policy is incompatible with .repomixignore.");
  }
  if (rootPath.maxDepth !== undefined || rootPath.maxFileSizeBytes !== undefined) {
    fail("stored depth/size limits are incompatible with the approved policy.");
  }
  if (!absentOrFalse(rootPath.follow)) {
    fail("stored path policy follows symbolic links.");
  }
}

function assertApprovedManifest(manifest, workspaceRoot, home) {
  if (extraKeys(manifest, MANIFEST_KEYS).length > 0) {
    fail("manifest schema is unknown.");
  }
  if (manifest.manifestVersion !== APPROVED_MANIFEST_VERSION) {
    fail("manifest schema version is unknown.");
  }
  if (typeof manifest.id !== "string" || manifest.id.length === 0) {
    fail("manifest schema is unknown.");
  }
  if (typeof manifest.name !== "string" || manifest.name.length === 0) {
    fail("manifest schema is unknown.");
  }
  if (typeof manifest.path !== "string" || resolve(manifest.path) !== resolve(home)) {
    fail("manifest index path is not this workspace index.");
  }
  if (manifest.indexPolicy !== APPROVED_INDEX_POLICY) {
    fail("stored index policy is not enabled.");
  }
  if (manifest.indexVersion !== APPROVED_INDEX_VERSION) {
    fail("manifest index version is unknown.");
  }
  if (typeof manifest.createdTime !== "number" || !Number.isFinite(manifest.createdTime)) {
    fail("manifest schema is unknown.");
  }
  if (typeof manifest.updatedTime !== "number" || !Number.isFinite(manifest.updatedTime)) {
    fail("manifest schema is unknown.");
  }
  if (!Array.isArray(manifest.rootPaths) || manifest.rootPaths.length !== 1) {
    fail("stored file-selection root set is incompatible with the approved policy.");
  }
  assertApprovedEmbedding(manifest.embedding);
  assertApprovedRuntime(manifest.embeddingRuntime);
  assertApprovedRootPath(manifest.rootPaths[0], workspaceRoot);
}

function assertNoAuthorization(home) {
  if (existsSync(join(home, AUTHORIZATION_FILE))) {
    fail("persisted Remote Embedding authorization state is present.");
  }
}

function parseIgnoreRules(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0 && !line.startsWith("#"));
}

function assertApprovedIgnorePolicy(workspaceRoot) {
  const ignoreFile = join(workspaceRoot, ".repomixignore");
  if (!existsSync(ignoreFile) || !statSync(ignoreFile).isFile()) {
    fail("current .repomixignore is missing, so stored documents cannot be proven compatible.");
  }
  let text;
  try {
    text = readFileSync(ignoreFile, "utf8");
  } catch {
    fail("current .repomixignore is unreadable, so the privacy boundary cannot be proven.");
  }
  const rules = parseIgnoreRules(text);
  if (rules.some((rule) => rule.startsWith("!"))) {
    fail("current .repomixignore contains a negation rule that can weaken the privacy boundary.");
  }
  const present = new Set(rules);
  if (REQUIRED_REPOMIXIGNORE_EXCLUSIONS.some((required) => !present.has(required))) {
    fail("current .repomixignore is missing required privacy exclusions.");
  }
}

async function assertStoredDocuments(workspaceRoot) {
  let zg;
  try {
    zg = await createZvecGrep({ root: workspaceRoot });
  } catch {
    fail("zg 0.2.1 cannot open this workspace index for read-only inspection.");
  }

  try {
    let info;
    try {
      info = await zg.info({ includeStatus: true });
    } catch {
      fail("zg 0.2.1 cannot prove that stored documents match the current file-selection policy.");
    }
    if (info.indexed !== true) {
      fail("existing index is not ready, so stored documents cannot be proven compatible.");
    }
    const status = info.status;
    if (!status || typeof status.filesDeleted !== "number" || !Number.isFinite(status.filesDeleted)) {
      fail("zg 0.2.1 cannot prove that stored documents match the current file-selection policy.");
    }
    if (status.filesDeleted > 0) {
      fail(
        "stored documents are not proven to match the current .repomixignore and file-selection policy.",
      );
    }
  } finally {
    await zg.close();
  }
}

export async function inspectZgPreflight(workspaceRoot = process.cwd()) {
  const root = resolve(workspaceRoot);
  assertApprovedIgnorePolicy(root);

  const home = join(root, ZG_DIR);
  if (!existsSync(home)) {
    return { ok: true, status: "missing-index" };
  }
  let homeStat;
  try {
    homeStat = statSync(home);
  } catch {
    fail("existing .zvec-grep/ cannot be inspected.");
  }
  if (!homeStat.isDirectory()) {
    fail("existing .zvec-grep path is not a directory.");
  }

  assertNoAuthorization(home);

  const manifestPath = join(home, MANIFEST_FILE);
  if (!existsSync(manifestPath)) {
    fail("manifest is missing where an index directory already exists.");
  }
  const manifest = readJsonObject(manifestPath);
  assertApprovedManifest(manifest, root, home);
  await assertStoredDocuments(root);
  return { ok: true, status: "approved-index" };
}

export function formatPreflightFailure(reason) {
  return [
    "zg preflight: existing index requires operator review.",
    reason,
    "Mismatch or unknown state blocks use of the index.",
    OPERATOR_ACTIONS,
  ].join("\n");
}

export async function runZgPreflight(workspaceRoot = process.cwd(), io = process) {
  try {
    const result = await inspectZgPreflight(workspaceRoot);
    if (result.status === "missing-index") {
      io.stdout.write(
        "zg preflight: no existing index; a new index may be created with the approved policy.\n",
      );
    } else {
      io.stdout.write("zg preflight: existing index matches the approved local Potion policy.\n");
    }
    return 0;
  } catch (error) {
    const reason =
      error instanceof ZgPreflightError
        ? error.reason
        : "existing index could not be proven compatible.";
    io.stderr.write(`${formatPreflightFailure(reason)}\n`);
    return 1;
  }
}

const invokedDirectly =
  process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (invokedDirectly) {
  const root = process.argv[2] ?? process.cwd();
  process.exit(await runZgPreflight(root));
}
