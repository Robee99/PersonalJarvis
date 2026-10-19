# Earth textures for the Orbital screen

Used by `src/components/stark/SpaceEarth.tsx`, served at `/space/...`.

| File | Size | Source |
|---|---|---|
| `earth-day.jpg` | 2048×1024 | NASA Blue Marble (Visible Earth) |
| `earth-night.jpg` | 2048×1024 | NASA Black Marble / Earth at Night (Visible Earth) |
| `earth-water.jpg` | 1024×512 | Water mask derived from NASA Blue Marble |

NASA imagery is in the public domain (NASA media usage guidelines). The
files were taken from the `example/img` folder of the `three-globe` npm
package (2.45.6, MIT licence), which redistributes the NASA images, then
downscaled and re-encoded as JPEG.

Clouds and stars are drawn in code, not from images.
