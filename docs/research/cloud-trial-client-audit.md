# Cloud trial client audit

Historical pinned-client audit; the integration and hosted deployment now
exist. See [current status](../current-status.md) for observed whole-library
behavior. The commands/schema/secrets analysis below remains relevant to the
pinned client; it does not document an implemented cloud-upload feature.

Researched 2026-10-03 against primary source and GitHub release metadata. No external application code was downloaded or executed, no account was paired, and no account documents were fetched in this audit. Source text and repository/release metadata were read for inspection.

## Pin and Windows availability

Read-only `git ls-remote` resolved:

- `v0.0.35`: `74a8e2ec7f324655ef3a3890936ed78e6e13ee51`.
- Current `master`: `aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373`.

The latest published release is v0.0.35. Windows x64 **is available**, despite the README still describing only Linux/macOS binaries. Its asset is `rmapi-win64.zip`, size 10,345,886 bytes, published 2026-08-19, SHA-256 `f8132c68044f054b2cca14c30d4525ea0fd78b2af175998d2aad5b3c8afcb05b`. The HTML asset digest and GitHub REST API `digest` field agree. This pins bytes from the upstream release; no independent signature or provenance attestation was verified. A Windows ARM64 asset also exists. [Release](https://github.com/ddvk/rmapi/releases/tag/v0.0.35), [asset metadata](https://github.com/ddvk/rmapi/releases/expanded_assets/v0.0.35), [GitHub release API](https://api.github.com/repos/ddvk/rmapi/releases/tags/v0.0.35)

The tagged workflow cross-compiles Windows amd64/arm64 using Go 1.23.1 and packages `rmapi.exe` inside the ZIP. A local Go build is unnecessary for this trial. [Pinned release workflow](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/.github/workflows/release.yml)

## Exact command contract

Global flags must precede the command. Verified at the release tag:

```text
rmapi.exe version
rmapi.exe -ni -json find /
rmapi.exe -ni get --id DOCUMENT_UUID
```

`-ni` prevents requesting a pairing code. `-json` controls the shell's JSON output; the `find` subcommand does not implement its own JSON flag. `get --id` resolves an exact document ID and writes `<cloud display name>.rmdoc` in its working directory; it has no destination argument. Never substitute an arbitrary command supplied by the user into a general rmapi shell. The executable also supports destructive commands, even when the wrapper only offers reads. [Pinned entry point](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/main.go), [pinned find](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/shell/find.go), [pinned get](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/shell/get.go)

Find returns the node array documented in the README (`id`, `name`, `type`, `version`, `modifiedClient`, `parent`, tags and other display properties). The document `version` or modified date is not the complete immutable content revision. Prefer the cloud index's document hash for change identity. [JSON contract](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/README.md), [document conversion and hash](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/blobdoc.go)

## Pairing and local credentials

There is no standalone pair command. Invoke a read command without `-ni` on a new private profile; the process requests one eight-character code on stdin, registers a device token, then obtains a user token. Deliver the code as subprocess stdin, never an argument, environment variable, shell history entry or log. Use `https://my.remarkable.com/device/browser/connect`, matching the release prompt and configured current client route. The initial recommendation of `/generator-device` was incorrect: live browser verification on 2026-10-03 reproduced its 404 and confirmed that `/device/browser/connect` reaches reMarkable's sign-in flow. The CLI uses `api/auth.go`, not the newer separate `auth` package. [Release authentication](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/auth.go), [current URL configuration](https://github.com/ddvk/rmapi/blob/aa60dac8a8dbb1b4eb6a25f2caf2f3daea573373/config/url.go), [pairing page](https://my.remarkable.com/device/browser/connect).

`RMAPI_CONFIG` chooses the YAML token-file path. Otherwise an existing home `.rmapi` wins; otherwise `os.UserConfigDir()/rmapi/rmapi.conf` is used. Token writes use mode 0600, but Windows ACL protection requires an explicit private profile directory. Upstream `SaveTokens` ignores the `os.WriteFile` result, so RemarkSync must verify that pairing produced a readable nonempty token store instead of trusting exit status alone. User-token renewal can rewrite this file. [Pinned token configuration](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/config/config.go)

Automatic cache writes go to `os.UserCacheDir()/rmapi/tree.cache`; Windows uses `LOCALAPPDATA`. The JSON cache contains document metadata, content summaries and complete per-file index entries, not just IDs. It is written mode 0644, with its directory created 0700; isolate it under the protected profile and set child `LOCALAPPDATA` accordingly. No dedicated rmapi cache-path environment variable was found. The cache is shared across invocations by default, so profiles need isolation and serialized access. [Pinned cache](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/common.go)

## Logs and environment controls

`RMAPI_TRACE=1` logs authentication tokens and HTTP request/response dumps, including Authorization headers and pairing payloads. `RMAPI_TRACE=2` writes informational logs to stdout. With tracing absent, info/trace are discarded, warnings still go to stdout, errors to stderr. Therefore force tracing off, avoid recording raw subprocess output in general logs, and do not assume stdout is unconditionally a clean JSON array. [Pinned logging](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/log/log.go), [pinned transport](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/transport/transport.go)

Clear inherited `RMAPI_AUTH`, `RMAPI_DOC` and `RMAPI_HOST`: they redirect authentication or document endpoints. Set a bounded positive concurrency, such as `RMAPI_CONCURRENT=4`, and clear `RMAPI_FORCE_SCHEMA_VERSION`. Set child `TMP`/`TEMP` to a private temporary directory. Upstream fetch creates `rmapizip*` in OS temp and may leave incomplete files on errors. Restrict ordinary diagnostics to operation, exit status and safe error categories. [Pinned endpoints](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/config/url.go), [fetch implementation](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/apictx.go)

## Read-only behavior and completeness

For sync15 accounts, startup constructs a mirror using only `GetRootIndex` and `GetReader`, then saves the local cache. Find reads that in-memory tree. Get downloads each indexed blob into a ZIP; neither invokes upload, remote deletion or root publication. Authentication does use registration/renewal POSTs and changes local tokens. Thus “read-only” means no cloud document mutations, not literally no HTTP POSTs or local writes. [Startup and fetch](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/apictx.go), [read-only mirror interface](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/remotestorage.go), [mirror](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/tree.go), [GET storage methods](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/blobstorage.go)

`.rmdoc` is a ZIP whose entry names are the original indexed file names, including metadata/content, base PDF/EPUB and page scene paths when present in the cloud index. The upstream client copies all indexed blobs, but does not independently verify their SHA-256 after download. Validate ZIP entry safety/duplicates/limits, then compare file identities and hashes to the mirrored source manifest; validate JSON and PDF and scene decoding before accepting a revision. Cloud-index presence alone does not establish that this account uploaded every expected annotation. [Fetch archive](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/apictx.go), [index entry format](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/entry.go)

Two upstream caveats matter for a robust wrapper. First, `get` uses the refreshed cloud display name unsanitized; validating the name during an earlier `find` leaves a rename race. A strict implementation should obtain validated per-file entries from the mirrored index and download by content hash itself, or enforce actual OS write confinement around `get`. Second, a warm-cache `BlobDoc.Mirror` updates changed hashes but leaves existing entry sizes stale. A fresh isolated cache avoids relying on stale sizes. Source SHA-256 validation remains preferable. [Unsafe filename construction](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/shell/get.go), [warm-cache update](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/blobdoc.go)

## Recommended trial boundary

### Exact cache and authenticated read adapter

The token YAML keys are `devicetoken` and `usertoken`; use only the latter as the bearer credential for document reads. The cache is Go JSON with **capitalized** exported field names. Embedded `Entry` fields are flattened into each document. The following is illustrative schema, not captured account data:

```json
{
  "Hash": "ROOT_INDEX_SHA256",
  "Generation": 123,
  "SchemaVersion": "4",
  "CacheVersion": 3,
  "Docs": [
    {
      "Hash": "DOCUMENT_INDEX_SHA256",
      "Type": "0",
      "DocumentID": "DOCUMENT_UUID",
      "Subfiles": 3,
      "Size": 123456,
      "Metadata": {},
      "Content": {},
      "Files": [
        {
          "Hash": "FILE_BYTES_SHA256",
          "Type": "0",
          "DocumentID": "DOCUMENT_UUID.content",
          "Subfiles": 0,
          "Size": 1234
        }
      ]
    }
  ]
}
```

`Docs[i].DocumentID` is the document UUID. Its `Hash` is the source document index identity and the root `Hash`/`Generation` identify the listing snapshot. `Files[j].DocumentID` is the complete raw file path to use inside a safe archive, not a UUID to rename. Read these properties directly instead of inferring revisions from display timestamps. [Entry fields](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/entry.go), [HashTree fields](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/tree.go), [BlobDoc fields](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/blobdoc.go), [token keys](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/model/auth.go)

The release uses these HTTPS reads:

```text
GET https://internal.cloud.remarkable.com/sync/v4/root
GET https://internal.cloud.remarkable.com/sync/v3/files/{entry.Hash}
Authorization: Bearer {usertoken}
rm-filename: {entry.DocumentID}   # blob requests
User-Agent: rmapi
```

Require a strictly validated 64-character hexadecimal hash and safe header/path values, stream each blob to private staging, bound byte counts, and verify the resulting SHA-256. Do not follow an arbitrary server-supplied URL or inherit endpoint overrides. A direct Python read adapter can construct the ZIP using safe known archive names and avoids upstream `get`'s display-name write hazard. Treat 401/403 as an authentication failure; refresh through a controlled rmapi listing, then retry a bounded number of times. Require an entirely fresh per-run cache and refuse absent, malformed or inconsistent cache data after listing. [Pinned endpoint construction](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/config/url.go), [GET root and stream](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/api/sync15/blobstorage.go), [bearer/header transport](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/transport/transport.go)

Pair once with a masked UI input and private token/cache storage outside Git, list the account, choose one existing annotated PDF, preserve its validated raw archive in a new local revision, and export it using RemarkSync's own renderer. Never mirror cloud absence into local deletion. Compare the raw scenes and original PDF with the existing USB acquisition where possible. Live compatibility, token renewal and this account's sync coverage remain untested by this audit.

### Cache/listing validation details

`find /` returns a plain lower-camel-case JSON node array. It includes synthetic collection nodes for root (`id=""`) and trash (`id="trash"`), which are not inserted into cache `Docs`. Deleted/trashed nodes are filtered differently; validate each listed `DocumentType` UUID against its fresh cached document, rather than requiring total ID-set equality. [Pinned JSON formatter](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/shell/output.go), [pinned tree traversal](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/filetree/treeutil.go).

Cache `Content`/`Metadata` are lossy Go struct projections: unknown fields disappear, `Transform` has `json:"-"`, and cPages entries omit source fields. Preserve and parse exact downloaded `.content`/`.metadata` blobs; use the cache for acquisition identity/names/hashes/sizes only. [Pinned content schema](https://github.com/ddvk/rmapi/blob/74a8e2ec7f324655ef3a3890936ed78e6e13ee51/archive/file.go).
