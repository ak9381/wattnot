# rack/ai

`rack.jpg` is an AI-generated illustration, not a photo of a real rack.
`masks.manual.json` is a hand-checked layout of its 10 server units.

SAM 3.1 run: "rack server" for the rack frame and one point per unit for the
10 slots. Worst IoU against the manual layout: 0.849 (4 calls).
