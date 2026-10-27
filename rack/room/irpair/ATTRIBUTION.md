# Attribution

Real visible + infrared pair of a server rack (hot aisle, a university data centre).

- Figure 3 (a) visible, (b) FLIR ONE Pro thermogram, peak 41.5 C shown in the image.
- Paper: Ding et al., "Analysis of Anthropogenic Waste Heat Emission from an Academic Data Center", Energies 2024, 17(8), 1835. https://doi.org/10.3390/en17081835
- Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), confirmed from the Crossref record for the DOI.
- `visible.jpg` and `thermal.jpg` are crops of panels (a) and (b) of the published figure, otherwise unchanged. The red annotation arrows in (a) and the FLIR on-screen readout in (b) are part of the original figure.
- The two panels are not pixel-registered (different framing), so no SAM mask is carried from one to the other.
- `ir-read.json` is our estimate from the thermal panel's colours (range assumed 20 to 41.5 C). The 41.5 C top of that range is the figure's own label, so the max in the file is the calibration, not a check.
