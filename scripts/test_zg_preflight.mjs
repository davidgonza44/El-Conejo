#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import {
  APPROVED_DIMENSION,
  APPROVED_IGNORE_FILES,
  APPROVED_INDEX_POLICY,
  APPROVED_INDEX_VERSION,
  APPROVED_INSENSITIVE_GLOBS,
  APPROVED_MANIFEST_VERSION,
  APPROVED_METRIC,
  APPROVED_MODEL,
  APPROVED_PROVIDER,
  REPO_ROOT,
  REQUIRED_REPOMIXIGNORE_EXCLUSIONS,
  runZgPreflight,
} from "./zg_preflight.mjs";

// Test-only fixture helper. Production zg:preflight / zg:index never import this.
// Removing it would require creating real Potion indexes for every synthetic case.
const ZG_STORAGE = await import(
  pathToFileURL(
    join(REPO_ROOT, "node_modules/@zvec/zvec-grep/dist/engine/storage/index.js"),
  ).href
);

const SECRET = "ZG_PREFLIGHT_SECRET_VALUE_9f3a";
const REMOTE_ENDPOINT = "https://example.test/embeddings?token=should-not-leak";

function privacyPolicyLines({ omit = [], extra = [], negation = [], comments = false } = {}) {
  const required = REQUIRED_REPOMIXIGNORE_EXCLUSIONS.filter((rule) => !omit.includes(rule));
  return [
    ...(comments ? ["# privacy boundary", "", "# trailing comment after blanks"] : []),
    ...required,
    ...extra,
    ...negation,
    "",
  ];
}

async function writePrivacyPolicy(root, options = {}) {
  const joiner = options.crlf ? "\r\n" : "\n";
  await writeFile(join(root, ".repomixignore"), privacyPolicyLines(options).join(joiner));
}

async function createWorkspace() {
  const root = await mkdtemp(join(tmpdir(), "zg-preflight-"));
  await writePrivacyPolicy(root, { extra: ["later-private.txt"] });
  await writeFile(join(root, "keep.ts"), "export const keep = true;\n");
  return root;
}

function assertNoPrivateDiagnostics(output) {
  assertNoSecrets(output);
  assert.doesNotMatch(output, /\.env|uploads\/\*\*|instance\/\*\*|private_imports|reports\/generated|tests\/tmp|tests\/\.tmp|90_archivo_no_usar/);
}

function approvedManifest(root) {
  const home = join(root, ".zvec-grep");
  const now = Date.now();
  return {
    manifestVersion: APPROVED_MANIFEST_VERSION,
    id: "approved-workspace-id",
    name: "approved",
    path: home,
    rootPaths: [
      {
        absolutePath: root,
        recursive: true,
        ignoreFiles: [...APPROVED_IGNORE_FILES],
        insensitiveGlobs: [...APPROVED_INSENSITIVE_GLOBS],
      },
    ],
    indexPolicy: APPROVED_INDEX_POLICY,
    embedding: {
      provider: APPROVED_PROVIDER,
      model: APPROVED_MODEL,
      dimension: APPROVED_DIMENSION,
      metric: APPROVED_METRIC,
    },
    indexVersion: APPROVED_INDEX_VERSION,
    createdTime: now,
    updatedTime: now,
    embeddingRuntime: {
      device: "auto",
    },
  };
}

async function writeManifest(root, manifest) {
  const home = join(root, ".zvec-grep");
  await mkdir(home, { recursive: true });
  await writeFile(join(home, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
}

async function writeApprovedStorage(root, mutate) {
  const home = join(root, ".zvec-grep");
  await mkdir(home, { recursive: true });
  const storage = ZG_STORAGE.createWorkspaceIndexStorage({
    storagePath: home,
    readOnly: false,
    embedding: {
      provider: APPROVED_PROVIDER,
      model: APPROVED_MODEL,
      dimension: APPROVED_DIMENSION,
      metric: APPROVED_METRIC,
    },
  });
  try {
    mutate?.(storage, root);
  } finally {
    storage.close();
  }
}

async function writeApprovedIndex(root, mutateStorage, manifestOverrides = {}) {
  const manifest = {
    ...approvedManifest(root),
    ...manifestOverrides,
    embedding: {
      ...approvedManifest(root).embedding,
      ...(manifestOverrides.embedding ?? {}),
    },
    embeddingRuntime: {
      ...approvedManifest(root).embeddingRuntime,
      ...(manifestOverrides.embeddingRuntime ?? {}),
    },
    rootPaths: manifestOverrides.rootPaths ?? approvedManifest(root).rootPaths,
  };
  await writeManifest(root, manifest);
  await writeApprovedStorage(root, mutateStorage);
  return manifest;
}

function addStoredFile(storage, root, relativePath, extras = {}) {
  storage.markFileDirty({
    id: extras.id ?? `stored-${relativePath.replace(/[^A-Za-z0-9._-]+/g, "-")}`,
    absolutePath: join(root, relativePath),
    relativePath,
    rootPath: root,
    sizeBytes: 4,
    lastModifiedTime: Date.now(),
    kind: extras.kind ?? "text",
    format: extras.format ?? "text",
  });
}

async function capturePreflight(root) {
  const stdout = [];
  const stderr = [];
  const code = await runZgPreflight(root, {
    stdout: { write: (chunk) => stdout.push(String(chunk)) },
    stderr: { write: (chunk) => stderr.push(String(chunk)) },
  });
  return {
    code,
    stdout: stdout.join(""),
    stderr: stderr.join(""),
  };
}

function assertNoSecrets(output) {
  assert.equal(output.includes(SECRET), false);
  assert.equal(output.includes(REMOTE_ENDPOINT), false);
  assert.match(output, /^[\s\S]*$/);
}

async function withWorkspace(fn) {
  const root = await createWorkspace();
  try {
    return await fn(root);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

test("required privacy exclusions match the repository contract", () => {
  assert.deepEqual([...REQUIRED_REPOMIXIGNORE_EXCLUSIONS], [
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
});

test("fresh workspace with valid privacy policy and no index passes", async () => {
  await withWorkspace(async (root) => {
    const result = await capturePreflight(root);
    assert.equal(result.code, 0);
    assert.match(result.stdout, /no existing index/);
    assert.equal(result.stderr, "");
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("fresh workspace missing a required privacy exclusion fails", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, { omit: ["instance/**"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /privacy exclusions/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("fresh workspace missing .env fails", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, { omit: [".env"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /privacy exclusions/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("fresh workspace missing .env.* fails", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, { omit: [".env.*"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /privacy exclusions/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("approved index with a required privacy rule removed fails", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root);
    await writePrivacyPolicy(root, { extra: ["later-private.txt"], omit: ["uploads/**"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /privacy exclusions/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("approved index with .env removed fails", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root);
    await writePrivacyPolicy(root, { extra: ["later-private.txt"], omit: [".env"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /privacy exclusions/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("repomixignore negation or re-inclusion rule fails", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, { extra: ["later-private.txt"], negation: ["!keep.ts"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /negation rule/);
    assert.doesNotMatch(result.stderr, /keep\.ts/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("extra harmless exclusion still passes", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, { extra: ["later-private.txt", "docs/architecture/*.html"] });
    const result = await capturePreflight(root);
    assert.equal(result.code, 0);
    assert.match(result.stdout, /no existing index/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("comments, blank lines, and CRLF do not fail a valid privacy policy", async () => {
  await withWorkspace(async (root) => {
    await writePrivacyPolicy(root, {
      extra: ["later-private.txt"],
      comments: true,
      crlf: true,
    });
    const result = await capturePreflight(root);
    assert.equal(result.code, 0);
    assert.match(result.stdout, /no existing index/);
    assertNoPrivateDiagnostics(`${result.stdout}${result.stderr}`);
  });
});

test("approved index passes", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root);
    const result = await capturePreflight(root);
    assert.equal(result.code, 0);
    assert.match(result.stdout, /approved local Potion policy/);
    assert.equal(result.stderr, "");
    assertNoSecrets(`${result.stdout}${result.stderr}`);
  });
});

test("different stored model fails", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root, undefined, {
      embedding: { model: "all-minilm-l6-v2", dimension: 384 },
    });
    const before = await readFile(join(root, ".zvec-grep", "manifest.json"), "utf8");
    const result = await capturePreflight(root);
    const after = await readFile(join(root, ".zvec-grep", "manifest.json"), "utf8");
    assert.equal(result.code, 1);
    assert.match(result.stderr, /local\/potion-code-16m-v2/);
    assert.match(result.stderr, /--rebuild|--reset-paths|--drop/);
    assert.doesNotMatch(result.stderr, /running --rebuild|executing --drop|automatically/);
    assert.equal(after, before);
    assertNoSecrets(`${result.stdout}${result.stderr}`);
  });
});

test("remote provider or runtime fails", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root, undefined, {
      embedding: {
        provider: "qwen",
        model: "qwen3.7-text-embedding",
        dimension: 1024,
      },
      embeddingRuntime: {
        device: undefined,
        apiKey: SECRET,
        endpoint: REMOTE_ENDPOINT,
      },
    });
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /provider\/runtime|remote|credential/i);
    assertNoSecrets(`${result.stdout}${result.stderr}`);
  });
});

test("corrupt or unknown manifest fails", async () => {
  await withWorkspace(async (root) => {
    await mkdir(join(root, ".zvec-grep"), { recursive: true });
    await writeFile(join(root, ".zvec-grep", "manifest.json"), "{not-json");
    const corrupt = await capturePreflight(root);
    assert.equal(corrupt.code, 1);
    assert.match(corrupt.stderr, /corrupt|unknown|missing/i);

    await writeApprovedIndex(root, undefined, { manifestVersion: 999 });
    const unknown = await capturePreflight(root);
    assert.equal(unknown.code, 1);
    assert.match(unknown.stderr, /unknown/);
    assertNoSecrets(`${corrupt.stdout}${corrupt.stderr}${unknown.stdout}${unknown.stderr}`);
  });
});

test("incompatible path policy fails", async () => {
  await withWorkspace(async (root) => {
    const manifest = approvedManifest(root);
    manifest.rootPaths[0] = {
      ...manifest.rootPaths[0],
      hidden: true,
      include: ["src/**"],
      globs: ["**/*.md"],
      ignoreFiles: ["old-ignore"],
    };
    await writeManifest(root, manifest);
    await writeApprovedStorage(root);
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /path policy|ignore-file|iglob|hidden|include/i);
    assertNoSecrets(`${result.stdout}${result.stderr}`);
  });
});

test("stale private, image, or ignore-file state fails", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root, (storage, workspace) => {
      addStoredFile(storage, workspace, "later-private.txt");
    });
    const staleIgnore = await capturePreflight(root);
    assert.equal(staleIgnore.code, 1);
    assert.match(staleIgnore.stderr, /stored documents|private|policy/i);

    await writeApprovedIndex(root, (storage, workspace) => {
      addStoredFile(storage, workspace, "uploads/secret.txt");
    });
    const stalePrivate = await capturePreflight(root);
    assert.equal(stalePrivate.code, 1);
    assert.match(stalePrivate.stderr, /private|stored documents/i);

    await writeApprovedIndex(root, (storage, workspace) => {
      addStoredFile(storage, workspace, "photo.png", { kind: "image", format: "png" });
    });
    const staleImage = await capturePreflight(root);
    assert.equal(staleImage.code, 1);
    assert.match(staleImage.stderr, /stored documents|policy/i);

    const combined = `${staleIgnore.stdout}${staleIgnore.stderr}${stalePrivate.stdout}${stalePrivate.stderr}${staleImage.stdout}${staleImage.stderr}`;
    assert.doesNotMatch(combined, /uploads\/secret|later-private|photo\.png/);
    assertNoSecrets(combined);
  });
});

test("stdout and stderr never echo persisted secrets", async () => {
  await withWorkspace(async (root) => {
    const manifest = approvedManifest(root);
    manifest.embeddingRuntime = {
      apiKey: SECRET,
      endpoint: REMOTE_ENDPOINT,
    };
    await writeManifest(root, manifest);
    await writeApprovedStorage(root);
    const result = await capturePreflight(root);
    assert.equal(result.code, 1);
    assertNoSecrets(`${result.stdout}${result.stderr}`);
    assert.doesNotMatch(result.stderr, /example\.test/);
  });
});

test("preflight does not rebuild, reset, or drop an incompatible index", async () => {
  await withWorkspace(async (root) => {
    await writeApprovedIndex(root, undefined, {
      embedding: { model: "all-minilm-l6-v2", dimension: 384 },
    });
    const manifestPath = join(root, ".zvec-grep", "manifest.json");
    const before = createHash("sha256")
      .update(await readFile(manifestPath))
      .digest("hex");
    const result = await capturePreflight(root);
    const after = createHash("sha256")
      .update(await readFile(manifestPath))
      .digest("hex");
    assert.equal(result.code, 1);
    assert.equal(after, before);
    assert.match(result.stderr, /read-only/);
    assert.doesNotMatch(result.stderr, /ran --rebuild|ran --reset-paths|ran --drop/);
  });
});

test("source of zg:index still keeps preflight plus approved CLI defenses", async () => {
  const pkg = JSON.parse(await readFile(join(REPO_ROOT, "package.json"), "utf8"));
  const preflightSource = await readFile(join(REPO_ROOT, "scripts/zg_preflight.mjs"), "utf8");
  assert.match(pkg.scripts["zg:preflight"], /zg_preflight\.mjs/);
  assert.match(pkg.scripts["zg:index"], /npm run zg:preflight && zg index/);
  assert.match(pkg.scripts["zg:index"], /--mode direct/);
  assert.match(pkg.scripts["zg:index"], /--embedding local\/potion-code-16m-v2/);
  assert.match(pkg.scripts["zg:index"], /--ignore-file \.repomixignore/);
  assert.match(pkg.scripts["zg:status"], /npm run zg:preflight && zg status/);
  assert.match(pkg.scripts["zg:status"], /--mode direct/);
  assert.match(pkg.scripts["zg:status"], /--check-ready/);
  assert.equal(pkg.scripts["zg:version"], "zg --version");
  assert.doesNotMatch(pkg.scripts["zg:version"], /zg:preflight/);
  for (const glob of APPROVED_INSENSITIVE_GLOBS) {
    assert.match(pkg.scripts["zg:index"], new RegExp(glob.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.match(preflightSource, /import \{ createZvecGrep \} from "@zvec\/zvec-grep"/);
  assert.doesNotMatch(preflightSource, /@zvec\/zvec-grep\/dist/);
  assert.doesNotMatch(preflightSource, /node_modules\/@zvec\/zvec-grep/);
  assert.doesNotMatch(preflightSource, /engine\/storage/);
  assert.doesNotMatch(preflightSource, /engine\/pipeline/);
  assert.doesNotMatch(preflightSource, /pathToFileURL/);
  assert.doesNotMatch(preflightSource, /createWorkspaceIndexStorage|getWorkspaceIndexStatus|listFiles\(/);
});
