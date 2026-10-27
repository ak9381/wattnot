# Rack photo

`rack.jpg` is "Rack of Cisco Hyperflex HXAF 240c M5 nodes.jpg" by Btrs, from
Wikimedia Commons, licensed CC BY-SA 4.0
(https://creativecommons.org/licenses/by-sa/4.0/).
Source: https://commons.wikimedia.org/wiki/File:Rack_of_Cisco_Hyperflex_HXAF_240c_M5_nodes.jpg

Changes: we took the 960 px wide Commons thumbnail and cropped it to rows
40 to 1440 (960x1400) to drop the floor. No other edits. This cropped photo is
shared under the same license, CC BY-SA 4.0.

The outlines in `masks.manual.json` are ours, drawn by eye on this photo
before the SAM calls. After the first comparison we rechecked the photo and
fixed one mistake: the bottom unit was drawn about 12 px too low. The ten units are mapped to GPU 0 to 9 for the demo;
they are not real GPU positions. The photo shows no power distribution unit,
so this layout has none. The bottom unit's outline includes its rack ears.

The rack audit (`rack/audit/R1.audit.jpg`, drawn on this photo, CC BY-SA 4.0) reads the same
ten units from SAM's boxes. The demo inventory in `rack/audit/fleet.json` lists only eight
of them (SIMULATED), so the audit has two units to report as not in the inventory.
