# CC-CEDICT for BOOX

Reproducible, unified Simplified/Traditional Chinese-to-English StarDict and
MDict 2.0 builds for the built-in Dictionary and NeoReader on BOOX firmware
4.1 or newer.

The generated dictionary uses real index entries for both Chinese forms. It
does not depend on StarDict synonym files, and it does not index pinyin or
English. Articles show the selected headword first, the alternate Chinese form,
tone-marked pinyin, and inline English senses separated by middle dots using
conservative HTML.

## Install a StarDict release on BOOX

1. Download `cc-cedict-boox-YYYY-MM-DD.zip` from GitHub Releases.
2. Extract the included `CC-CEDICT-Boox` directory beneath:

   ```text
   Internal shared storage/dicts/
   ```

3. Confirm this path exists:

   ```text
   Internal shared storage/dicts/CC-CEDICT-Boox/cc-cedict-boox.ifo
   ```

4. Open the BOOX Dictionary app.
5. Open its options, choose **Preferred Dictionary**, and enable
   **CC-CEDICT Chinese-English (Simplified + Traditional)**.
6. Restart the Dictionary app or reboot the device if it is not detected.

## Install an MDict release on BOOX

Install either the StarDict package above or the MDict file, not both, to avoid
duplicate dictionaries.

1. Download `cc-cedict-boox-YYYY-MM-DD.mdx` from GitHub Releases.
2. Create this directory on the device:

   ```text
   Internal shared storage/dicts/CC-CEDICT-Boox-MDict/
   ```

3. Copy the `.mdx` file into that directory.
4. Open the BOOX Dictionary app.
5. Open its options, choose **Preferred Dictionary**, and enable
   **CC-CEDICT Chinese-English (Simplified + Traditional)**.
6. Restart the Dictionary app or reboot the device if it is not detected.

## Set up the builder

Requirements:

- Python 3.11 or newer
- Git
- No runtime packages beyond the Python standard library

From the repository root:

```powershell
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The `cedict-boox` console command and `python -m cedict_boox` are equivalent.
The `test` extra installs a pinned independent MDX reader used only by the test
suite; production builds remain standard-library-only.

## Ingest a verified source snapshot

MDBG prohibits automated or scripted access to its website. Download the
verified CC-CEDICT V1 ZIP manually in a browser from the
[CC-CEDICT download page](https://www.mdbg.net/chinese/dictionary?page=cc-cedict),
then run:

```powershell
python -m cedict_boox ingest C:\Downloads\cedict_1_0_ts_utf-8_mdbg.zip
```

This validates every source entry, records hashes and release metadata, and
writes:

```text
data/upstream/cedict_1_0_ts_utf-8_mdbg.zip
data/upstream/source.json
```

Commit both files. The command rejects older snapshots and refuses a changed
archive carrying the same release date. If MDBG intentionally replaces a
same-date release after you have checked it, use:

```powershell
python -m cedict_boox ingest C:\Downloads\cedict_1_0_ts_utf-8_mdbg.zip `
  --allow-same-date-replacement
```

There is deliberately no URL or download option in the program or workflows.

## Build and verify

```powershell
python -m cedict_boox build
python -m cedict_boox verify dist\cc-cedict-boox-YYYY-MM-DD.zip
python -m cedict_boox verify dist\cc-cedict-boox-YYYY-MM-DD.mdx
```

Use a different destination or a rebuild revision when needed:

```powershell
python -m cedict_boox build --output artifacts --revision 2
```

The build writes the StarDict installation ZIP, the standalone MDict 2.0
`.mdx`, one `.sha256` sidecar for each, and `build-report.json`. Given the same
tracked source manifest, source archive, converter commit, and revision, output
bytes are deterministic across platforms.

## What validation covers

Ingestion fails on malformed V1 data, invalid UTF-8, unsafe or encrypted ZIP
members, duplicate source identities, overlong StarDict keys, and declared
entry-count mismatches. The output verifier independently checks:

- StarDict 2.4.2 metadata and exact counts
- UTF-8 headwords and articles
- exact `stardict_strcmp` index ordering
- big-endian 32-bit article offsets and sizes
- contiguous, fully referenced dictionary data
- the restricted HTML tag set
- the exact installation-package layout
- MDict 2.0 header, keyword blocks, record blocks, offsets, checksums, and
  compressed payloads

CI runs the tests on Windows and Linux, including an independent MDict reader.
When a tracked source is present, CI also builds twice, compares both artifact
SHA-256 values, verifies both formats, and uploads them as artifacts.

Merging a changed tracked source into `main` publishes a stable GitHub Release.
Manual release runs create `-r2`, `-r3`, and later converter rebuilds while
detecting identical source/commit releases idempotently.

## Licenses

The converter source code is under the [MIT License](LICENSE).

CC-CEDICT data and generated dictionary packages are under
[CC BY-SA 4.0](LICENSE-CC-BY-SA-4.0.txt). Attribution belongs to the
CC-CEDICT contributors; the verified distribution is provided by MDBG. The
MIT license for this converter does not override the dictionary-data license.
