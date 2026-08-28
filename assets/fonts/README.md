# Fraunces (display)

`fraunces-display.woff2` is the Fraunces variable font, instanced to
`opsz=144` and subset to the characters the landing page uses. 22 KB.

It is embedded in `docs/index.html` as a base64 `data:` URI at build time
rather than linked, because the pages must render from disk with no network
— they are recorded to video, often offline, and a webfont that fails to
load mid-take is a re-shoot.

Fraunces is licensed under the SIL Open Font License 1.1 (`Fraunces-OFL.txt`),
which permits embedding. Source: https://github.com/undercasetype/Fraunces
