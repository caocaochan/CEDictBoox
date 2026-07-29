# BOOX compatibility

Target: the built-in BOOX Dictionary and NeoReader on firmware 4.1 or newer.

Current status: **physical BOOX 4.1 validation pending**.

The generated format is deliberately conservative: StarDict 2.4.2, 32-bit
offsets, uncompressed `.idx` and `.dict`, UTF-8 HTML articles, and no `.syn`
file. The binary layout and installation package are covered by automated
tests, but a connected physical BOOX device is not available in this
repository environment.

## Physical smoke-test record

Fill one row for each tested device.

| Date | Device | Firmware | Dictionary app | NeoReader | Result/notes |
|---|---|---|---|---|---|
| Pending | Pending | 4.1+ | Pending | Pending | Physical validation pending |

For each device, test:

- Simplified `中国` and Traditional `中國`
- Identical-form `你好`
- Both readings and sense groups for `行`
- Tone marks and alternate-form presentation
- Selection from one Simplified and one Traditional Chinese EPUB in NeoReader
- Detection after closing the Dictionary app and after a cold restart

