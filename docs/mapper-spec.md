# WLED Mapper — working spec

This is the Nov 8, 2026 specification (v2) plus the decisions recorded while building it. Where they disagree, the recorded decisions win.

## Recorded decisions

- Routes live under `/api/mapper/`. That includes fixtures, controllers, pixelmap, highlight, effect, stop, ledmap, export, import, `POST /controllers/{id}/owner`, `POST /hooks/xschedule`, `POST /shows/{playlist}/start` and `/stop`, `POST /xlights/sync-controllers`, and mapper settings. The fleet session gate already covers `/api`.
- Live highlight and effect status use server-sent events at `GET /api/mapper/live`, with the same payload at `GET /api/mapper/status`. There is no WebSocket.
- The phone remembers the server URL and caches the fixture list in UserDefaults so the list does not flash empty. The password, when there is one, stays in the keychain. The ARWorldMap stays on the phone; the server stores the fixture JSON.
- `startIndex` is the index of the run's first pixel on that output, usually 0. It is not the controller-wide offset. The server adds that output's `hw.led.ins[].start` when it builds a DDP frame, a ledmap, or an xLights node number. Fixtures saved by the first mapper build, which copied the hardware start into `startIndex`, are converted on startup. The original JSON is copied to `migration-backups/fixtures-before-startindex/` first.
- A segment `pixelCountOverride` places exactly that many pixels along the span. The next span keeps the pitch spacing it would have had without the override.
- Helix capture is one tap per turn, on the same side of the trunk. Turns are the tap count minus one. The axis is fitted through the taps and shifted inward by the radius (entered, or a LiDAR circle fit) so the taps sit on the surface. Two stored points still mean the axis ends, which is how the regression tree is written.
- Counter-clockwise, looking up the trunk, is the spec formula with a positive sine term. Clockwise flips that sign.
- A raycast that misses does not pretend to be a confident tap. The point is stored with source `estimated` and low confidence, drawn in a different colour, and the capture screen says to use Nudge.
- LiDAR trunk radius uses mesh vertices within ±5 cm of the tap's height and within a short horizontal distance of the tap, then drops points that disagree with the fitted circle.
- Ledmaps upload only after the user confirms. Effects stay in the app and on the server. They can target the whole fixture, one labelled branch, or a height band (the example in the spec is 1.4 m).
- xLights export writes a 2D Custom Model and, when depth is greater than 1, pipe-separated layers. A Poly Line export is available for a run of straight pieces. `SourceVersion` is `2025.12`. xLights 2026.04 was read from the release notes (models became native objects, `parm1`/`parm2` were renamed but the old names are still read, and a 2026 file may wrap the model and drop the `CustomModel` attribute). No 2026.04 `.xmodel` could be obtained here, so the writer was not checked against a real 2026.04 file. `parm1`/`parm2` and `CustomWidth`/`CustomHeight` are both written.
- Camera detection is approach C. The server lights one pixel at a time, or `ceil(log2 N)` binary frames, over DDP. The phone finds blobs with Vision contours on the camera frame, and falls back to a brightest-blob scan. Rays use the ARKit intrinsics and camera pose. One view intersects the ray with the tapped model. Two views triangulate. A dark-room check runs before the pattern. Pixels that never appear are reported so the user can walk to a second viewpoint. Accepting writes `pixelCorrections` on the run (`source: detected`) without throwing away the path.
- CSV, Pixelblaze `[[x, y, z]]`, and an xLights custom-model grid import back into a fixture. The numbers screen edits waypoint coordinates and segment radius, turns, and pixel count.
- Drift: re-tap the opening reference at the end and spread the error along the waypoints. A saved `ARWorldMap` is loaded when the same fixture is captured again. If two waypoints are labelled as references, a later session asks for both of them before new points are added, and the app yaws the old points onto those taps. Roll stays fixed because gravity is up.
- The build-plan diagram in section 12 was not in the uploaded spec. The phases below are taken from that section and from the resource groups in section 14.

## Phases and the fixtures checked in for them

| Phase | What it is | Fixture |
| --- | --- | --- |
| Sanity | One straight metre | `fixtures/straight-1m.json` |
| 1 | A polyline on one output | `fixtures/wall-outline.json` |
| 2 | The branching tree, including two controllers | `fixtures/branching-tree.json`, `fixtures/two-controllers.json` |
| 3 | Surface taps around a helix | `fixtures/helix-taps.json` |
| 4 | Ledmap and xLights, derived from the fixtures above | the same files |
| 5 | One pixel moved by detection | `fixtures/detected-pixel.json` |

## Open questions, decided

- The server is the existing Python fleet service. Mapper routes extend it.
- Capture works on any iOS 17 phone. LiDAR mesh snapping is used when the phone has it; otherwise a plane raycast, then a depth nudge, then an `estimated` point.
- Each fixture has its own frame. There is no property-wide scene.
- A ledmap is uploaded only when the user confirms. Save does not reboot a controller.
- Spatial effects stay in the app and the server. They are not pushed into WLED's effect list.
- xLights gets a 2D custom model first. Depth layers and a Poly Line model are included. A true 2026.04 file was not available to verify.
- The product name is WLED Mapper.
- Tap-to-place is the capture path. Camera detection is the check when a pixel has to be exact.
- One sender at a time. Each controller has an owner, `fleet`, `xlights`, or `none`, stored in `controller_ownership`. Boards already in the fleet stay with the fleet; the original database is copied to `migration-backups/fleet-before-ownership.db` once. The renderer sends only while the owner is `fleet`. A ledmap upload reboots the board, so it is refused while the owner is `xlights`. Handing a board to xLights saves its WLED look. Handing it back to the fleet writes that look again.
- xSchedule is how the fleet learns a show is running. xSchedule calls `POST /api/mapper/hooks/xschedule` when a playlist starts or stops, and the phone calls `POST /api/mapper/shows/{playlist}/start` and `/stop`, which call xSchedule and set the owner on that show's boards. The fallback reads WLED `/json/info`: `live` true and `lip` different from this server's address means a show, and `live` ending hands that inferred board back. A show xSchedule or the phone started is not released by the fallback.
- The player is xSchedule. `GET /xScheduleQuery` and `GET /xScheduleCommand` are implemented (`GetPlayLists`, `GetPlayingStatus`, `Play specified playlist`, `Stop specified playlist`). FPP is the same player interface and is left unimplemented. The player URL, kind (`xschedule` or `fpp`), playlist name, and xLights show folder are server settings: `MAPPER_PLAYER_URL`, `MAPPER_PLAYER_KIND`, `MAPPER_PLAYLIST`, `MAPPER_XLIGHTS_SHOW`, or `PUT /api/mapper/settings`. The phone shows one Start/Stop control when a URL and a playlist are set, and hides it otherwise.
- The sync fixes gaps, and it previews before it writes. `POST /api/mapper/xlights/sync-controllers` reads `xlights_networks.xml` and the `Controller` attributes in `xlights_rgbeffects.xml`. The response is the diff. `apply: true` is the only write, and it writes a timestamped backup of each file first. It fills a channel count of 1, adds a fleet board the show does not list, updates an IP when the name still matches, and assigns a controller to a model only when one match is unambiguous. Ambiguous models are listed and left alone. A check at startup and then nightly runs the same diff, stores it, and surfaces it on `GET /api/status` as `xlightsDrift`. That check does not write.
- An xLights show import reads `xlights_rgbeffects.xml` as well as a single `.xmodel`. It asks for `scaleMetresPerUnit`. A new name is saved. An existing fixture is returned as a proposal. A phone capture (a waypoint tapped, snapped, typed, or estimated) is not overwritten unless the phone sends that proposal back.
- v2 calls show playback and ownership Phase 5 in the resource list. Camera detection, already built, stays the capture check. The two share a number in the spec; they are different work.

---

---

# Spatial LED Mapper — Developer Specification

Nov 8, 2026 · @Dylan

## 1. The problem

WLED knows the order the wire reaches each light. It knows nothing about where each light physically is. Effects need the second thing, and we only have the first.

Every WLED controller sees its lights as one numbered line: pixel 0, 1, 2 … up to the last pixel on that output. Every built-in effect is written against that line. A chase runs from index 0 upward. A gradient spreads from the first index to the last. That works perfectly when the strip is laid out as a straight line.

Real installations are not straight lines. The data wire takes whatever path the object forces on it:

- Up the trunk of a tree in a spiral, then up one branch, then jump across and **down** the next branch, then up the third.
- Around the outline of a wall, then back inward to draw a shape in the middle, all on one continuous strip.
- Split across two or more controller outputs because one output cannot drive enough pixels, or the wire cannot physically reach.

The moment the wire doubles back, splits, or spans outputs, index order and spatial order stop matching. The effect still runs correctly on the numbered line, but on the object it looks wrong: a chase goes up one branch and down the next; a gradient folds in half; two outputs each show their own copy of the effect side by side.

WLED has a partial fix called a **ledmap**, a file that re-orders the numbered line so WLED can treat the strip as if it were wired differently. It handles the "wire runs up and back down" case on one controller, and newer builds support a flat 2D grid. It cannot describe a 3D shape, and it cannot coordinate across controllers, because each controller only knows its own pixels.

What is missing is a layer that records **where every pixel is in real space**, across every controller and output, and uses that to drive effects, generate ledmaps, and hand the layout to other tools.

## 2. What we are building

An iOS app that lets you walk an LED installation with the phone, tap points along the actual strip, and produce a 3D map of where every pixel is. Everything else is derived from that map.

The product has three layers. Build them in this order; each one is useful on its own.

1. **Capture** — Record the physical path of each strip as an ordered list of 3D points, using the camera (ARKit) and, on supported phones, LiDAR. Attach the wiring facts: which controller, which output, how many pixels, which direction the data flows.
2. **Model** — Turn that path into a table: for every pixel on every output, an (x, y, z) position in one shared coordinate frame. Show it as a rotatable 3D preview so the user can confirm it matches reality.
3. **Use** — Consume the table to (a) generate WLED ledmap files for single-controller fixtures, (b) stream spatially correct effects to all controllers at once from the existing server, and (c) export an xLights model file.

**Who does what.** Each piece has one job, and the shared map is what connects them.

| Piece | Job | Never does |
| --- | --- | --- |
| Phone app | Measures where every pixel physically is; previews the map; is the everyday remote. | Talk to boards directly; render shows. |
| WLED Fleet server (existing) | Keeps boards healthy (discovery, backups, firmware); stores the map; plays everyday looks and position-based effects; hands boards to xLights and takes them back. | Sequence music-synced shows. |
| xLights / xSchedule | Choreographed, timed, music-synced shows, using models exported from the map. | Own board configuration or the map. |
| WLED firmware | Drives the pixels; runs its own effects when nobody is streaming. | Anything new. WLED is not modified. |

**Five rules the design follows.**

1. **The map is shared.** A fixture is mapped once with the phone and stored on the fleet. xLights receives it as a model export; a model edited in xLights can be imported back. Nobody redraws the tree by hand in two tools.
2. **Wiring comes from one place.** The fleet already knows every board, its outputs and pixel counts. Every export carries controller, protocol and start-channel details, so xLights drives the right board and port without anyone typing IPs or channel numbers.
3. **One sender at a time.** The fleet and xLights both stream DDP to the same boards; if both send, the lights fight. During a show the fleet pauses its effects and marks those boards "show running"; when the show ends it restores the normal looks.
4. **Everyday versus showtime.** Colours, brightness, quick looks and position-based effects (fill from bottom, height rainbow, branch chase) live in the fleet and the phone app. Timed sequences live in xLights. The phone can start or stop an xLights playlist, so it stays the single remote.
5. **Fleet care covers both.** Backups, firmware and finding a board are always the fleet's job, and a replaced or re-addressed board flows into the xLights controller list through the sync, so the show setup never drifts.

**Where things stand today.** The xLights show on the NAS has 5 WLED controllers, all on DDP. Three are placeholders with a channel count of 1, and 6 of the 12 models have no controller assigned. Rule 2 is aimed at exactly this: the first sync fills those in from the fleet.

**What this is not.** It is not a replacement for WLED's effect engine, not a sequencer like xLights, and not a general 3D scanning app. The app captures a *path*, not a mesh of the whole object. The tree's bark, the wall's texture and the room are irrelevant; only the line the wire takes matters.

**Who it is for.** First the owner of this installation, with a fleet of QuinLED Dig-Uno WLED controllers and an existing self-hosted server that already talks to all of them. Second, anyone with WLED and a shape that is not a straight line.

**Done looks like.** Stand in front of the tree, open the app, tap up the spiral and along each branch, hit Save, then select "Fill from bottom" and watch every branch light at the same height together, regardless of how the wire was run or how many controllers are involved.

## 3. Glossary

Read this once. Every later section uses these words exactly as defined here.

| Term | Meaning |
| --- | --- |
| **Pixel** | One individually addressable LED (or LED group) on a strip. WLED numbers them from 0. |
| **Pitch** | Spacing between pixels, usually given as LEDs per metre (30, 60, 144) or as a distance (16.7 mm for 60/m). |
| **Controller** | One WLED board (ESP32/ESP8266, e.g. QuinLED Dig-Uno). Has an IP address and one or more outputs. |
| **Output** | One physical data port on a controller. Drives one chain of pixels numbered from a start index. WLED calls this a "bus". |
| **Fixture** | One real-world lit object: the tree, the wall, the roofline. May span many controllers. |
| **Run** | One continuous chain of pixels on one output, from its first pixel to its last. |
| **Segment** | A stretch of a run with one geometric shape: a straight line, a curve, a helix, or a jump. |
| **Waypoint** | A 3D point the user placed. Segments connect consecutive waypoints. |
| **Jump** | A segment with wire but no pixels: the bare cable from the top of one branch to the top of the next. |
| **Direction** | Which way pixel numbers increase along a segment. "Up the trunk" and "down the branch" are opposite directions. |
| **Pixel map / coordinate table** | The output of the model: one row per pixel with controller, output, index, x, y, z. |
| **Ledmap** | WLED's remapping file (`ledmap.json`). An array where position = original index and value = new index; -1 = gap. |
| **DDP** | Distributed Display Protocol. A simple UDP protocol for streaming raw pixel colours to WLED in real time. |
| **E1.31 / sACN** | An older DMX-over-Ethernet standard WLED also accepts. 512 channels (170 RGB pixels) per universe. |
| **xLights** | Free sequencing software for light shows. Represents fixtures as "models" placed in a 2D/3D layout. |
| **ARKit** | Apple's framework for tracking the phone's position in space and placing virtual points on real surfaces. |
| **LiDAR** | Depth sensor on iPhone Pro / iPad Pro models. Gives ARKit a mesh of real surfaces so placed points land accurately. |
| **World anchor** | A fixed point in ARKit's coordinate system that stays put as the phone moves. |
| **Mesh snapping** | Projecting a tapped screen point onto the LiDAR mesh so the 3D point sits on the real surface, not in mid-air. |

## 4. What we must know about every installation

Eight facts fully describe any installation. Some come from the controller, some from the user, and the rest the phone measures.

| Fact | Why it matters | Where it comes from |
| --- | --- | --- |
| **Pixel count per output** | Tells us how many points to place along the path; lets us check the capture adds up. | WLED JSON API (`/json/info` → `leds.count`, and `/json/cfg` → `hw.led.ins[]` per output). |
| **Pixel pitch** (LEDs/m) | Converts distance along the path into pixel indices. 60/m means one pixel every 16.7 mm. | User picks from a list when adding the run (30, 60, 96, 144/m, or custom). Can also be derived: measured length ÷ known count. |
| **Start index and direction per run** | Where index 0 of this output sits and which way numbers grow. | User marks the first pixel when starting a run; direction follows the order waypoints are placed. |
| **The path** (sizes and shape) | The ordered 3D points the wire follows, plus how each span is shaped (straight, curved, spiral). | Phone capture: ARKit world tracking, LiDAR mesh snapping, camera taps. |
| **Jumps** | Where there is wire but no pixels, so indices do not advance while position does. | User taps "Jump" before placing the next waypoint. |
| **Surface type** | Lets us place pixels *on* the object (around a trunk, flat on a wall) rather than in a straight line between taps. | User picks per segment (see section 5); LiDAR can confirm a cylinder radius or a plane. |
| **Controller and output** | Ties every pixel back to the board and port that drives it, so the server knows where to send colour data. | Picked from the controller list the existing server already discovers. |
| **Shared origin and orientation** | All runs of one fixture must be in the same coordinate frame, and "up" must be up. | ARKit session origin + gravity alignment; multi-session captures are aligned by re-tapping a saved reference point. |

**Sizes** come from the path itself: the distance between waypoints is the length of lit strip (or of bare wire for a jump). For a spiral around a trunk, the app also needs the trunk radius at each turn, which LiDAR can measure or the user can enter.

**Directions** are implied by order. The user always places waypoints in the order the data wire travels, so "up" versus "down" a branch is never a separate setting.

**Representation of the object** is deliberately minimal. We do not model the tree. We model the *path on* the tree, plus a surface hint per segment so pixels wrap correctly between taps.

## 5. Shape and surface types

Five segment shapes cover every installation we can think of. Each needs a different handful of parameters and a different formula to place pixels between its waypoints.

| Shape | Real-world example | Parameters stored | How pixels are placed |
| --- | --- | --- | --- |
| **Straight** | Roofline, wall edge, shelf, a branch wrapped tightly enough to treat as a line | Start point, end point | Evenly spaced along the line, `count = length ÷ pitch`. |
| **Polyline** | Wall outline with a shape drawn inside; any strip laid in straight pieces with bends | Ordered list of corner points | Straight placement on each piece; pixel spacing carries across corners so no pixel is lost at a bend. |
| **Helix** (spiral) | Trunk wrap, column wrap, pole wrap | Axis start and end, radius at each turn (or taper), number of turns or pitch per turn, handedness (clockwise / counter-clockwise looking up) | Parametric spiral; arc length per turn = `√((2πr)² + h²)` where `h` is the rise per turn. |
| **Curve** | Arch, wreath, garland draped between hooks | Control points (3 or 4), fitted as a Catmull-Rom or Bézier spline | Walk the curve in small steps, drop a pixel every `pitch` of arc length. |
| **Jump** | Bare wire from one branch tip to the next; cable from controller to first pixel | Start point, end point, `pixels = 0` | Nothing placed. Position advances, index does not. |

**Surface hints** sit on top of the shape and tell the app which way the strip faces, which matters for effects that project an image or treat the fixture as a surface:

- **Planar** — wall, fence, garage door. The app fits a plane through the waypoints (or takes it from the LiDAR mesh) and records its normal. Enables 2D projection and WLED's 2D ledmap export.
- **Cylindrical** — trunk, column, pole. Records axis and radius. Enables "angle around" and "height" as effect coordinates, which is what makes a spiral-wrapped tree look right.
- **Branching** — the tree as a whole: a cylinder for the trunk, then child cylinders for each limb, connected by jumps. Not a new shape, just a grouping so effects can address "branch 2" or "all branches at height 1.4 m".
- **Freeform** — anything else (a bush, a wire-frame reindeer). No surface fitted; pixels simply have their 3D positions and effects use raw x, y, z.

**Why not a full 3D scan.** A LiDAR mesh of the whole object is large, noisy and mostly irrelevant. The five shapes above compress a 400-pixel tree into perhaps 15 waypoints and a dozen numbers, which is also what makes the model editable by hand and small enough to store on the controller.

## 6. The data model

Four nested objects hold everything: a Fixture owns Runs, a Run owns Segments, a Segment is bounded by Waypoints. The compiled pixel table is derived from them and never edited by hand.

**Fixture** — one real object.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID |  |
| `name` | string | "Front tree", "Garage wall" |
| `origin` | 3 floats + quaternion | Where ARKit's origin was when captured; used to re-align later sessions. |
| `surfaceHint` | enum | planar / cylindrical / branching / freeform (section 5) |
| `runs` | Run\[\] | Ordered as the user captured them. |

**Run** — one continuous pixel chain on one output.

| Field | Type | Notes |
| --- | --- | --- |
| `controllerId` | string | Matches the existing server's controller record (MAC or hostname). |
| `outputIndex` | int | Which port on the board (WLED bus number). |
| `startIndex` | int | Index of the first pixel in this run on that output. Usually 0. |
| `pixelCount` | int | Read from WLED; the capture must account for exactly this many. |
| `pitchMm` | float | 16.67 for 60/m. |
| `colorOrder` | string | GRB, RGB, RGBW — needed for streaming. |
| `segments` | Segment\[\] | In wire order. |

**Segment** — one shaped span between two waypoints.

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | enum | straight / polyline / helix / curve / jump |
| `waypointIds` | UUID\[\] | 2 for straight and jump; 3+ for polyline and curve; 2 (axis ends) for helix. |
| `params` | object | Shape-specific: `{turns, radiusStartMm, radiusEndMm, handedness}` for helix; `{controlPoints}` for curve. |
| `pixelCountOverride` | int? | Optional. Normally computed from length ÷ pitch; set it when the user knows the exact count on this span. |

**Waypoint** — a 3D point the user placed.

| Field | Type | Notes |
| --- | --- | --- |
| `position` | 3 floats (m) | In the fixture's coordinate frame, metres, y up. |
| `source` | enum | tapped / lidarSnapped / typed / imported |
| `confidence` | float | From ARKit tracking state at capture time; lets the UI flag shaky points. |
| `label` | string? | "Branch 2 tip", "Top of spiral" |

**Derived: PixelMap** — one row per pixel, regenerated whenever the model changes.

```csv
fixture,controller,output,index,x_m,y_m,z_m,segment,u
front-tree,dig-uno-07,0,0,0.000,0.120,0.210,trunk-helix,0.000
front-tree,dig-uno-07,0,1,0.016,0.121,0.208,trunk-helix,0.004
front-tree,dig-uno-07,0,2,0.031,0.123,0.205,trunk-helix,0.008
```

`u` is the pixel's position along its segment from 0 to 1, kept because effects like "chase along the branch" are easier with it than with raw coordinates.

**Storage.** Save the Fixture as one JSON document. It is small (kilobytes), human-readable, and easy to version. The PixelMap is a cache; recompute it on load rather than storing it. Store fixtures on the existing server so the phone, the server and the ledmap generator all read the same file.

## 7. Capture approaches

There are five ways to get waypoints into the model. Build AR tap-to-place first, add LiDAR snapping where the hardware allows, and treat camera pixel detection as the high-value second phase. All five write the same Waypoint records, so they can be mixed on one fixture.

| Approach | How it works | Strengths | Weaknesses | Use it for |
| --- | --- | --- | --- | --- |
| **A. AR tap-to-place** | ARKit tracks the phone in space. User aims the reticle at a point on the strip and taps; a raycast puts a 3D anchor where the ray meets a detected surface. | Works on every ARKit iPhone; cheap to build; user controls exactly which points matter. | Without LiDAR, raycasts only hit detected planes or feature points, so thin branches and dark trunks can miss. Drift of a few cm over a long walk. | Walls, rooflines, fences, anything with a clear surface behind the strip. The MVP. |
| **B. LiDAR mesh snapping** | Same tap flow, but with `sceneReconstruction = .mesh` on. ARKit builds a live mesh of real surfaces and raycasts with `.estimatedPlane` hit that mesh, so points land on curved or featureless surfaces too. | Points sit *on* the trunk or branch, not floating. The mesh also gives trunk radius for free: fit a circle to the mesh ring at a given height. | Pro/Pro Max and iPad Pro only. Mesh is coarse (centimetres), noisy on thin branches. | Trees, columns, poles, bushes. Anything cylindrical or irregular. |
| **C. Camera pixel detection** | Server lights pixels one at a time (or in binary-coded groups). Phone camera finds the bright blob in each frame, giving a 2D position per pixel. Two or more camera positions, or ARKit's known camera pose, triangulate to 3D. | Finds *every* pixel individually, so wiring mistakes, dead pixels and irregular spacing are captured exactly. No tapping. This is how Twinkly's app and MariMapper work. | Needs darkness; occluded pixels (behind the trunk) need a second viewpoint; takes a minute or two for a few hundred pixels. More computer vision to write. | Verifying a tapped model; dense or irregular fixtures; anything where exact per-pixel positions matter. |
| **D. Manual entry** | User types or edits numbers: lengths, radii, turn counts, or raw coordinates in a table. | Always available; exact when the user has measured with a tape. | Tedious; error-prone for anything with more than a dozen points. | Fixing a single bad waypoint; simple shapes (one straight strip); fallback when AR fails. |
| **E. Import** | Read an existing xLights model, a Pixelblaze map (JSON array of `[x,y,z]` per pixel), or a CSV. | Reuses work already done in other tools. | Only helps if that work exists. | Users migrating from xLights or Pixelblaze. |

**Recommended flow for the tree.** Open the fixture, pick the Dig-Uno and output, choose 60/m. Tap the first pixel at the base. Choose "Helix" and tap once per turn going up the trunk; the app fits a spiral through the taps and reads the radius from the LiDAR mesh. At the top, tap "Jump", walk to the first branch, tap its base, choose "Straight", tap the tip. "Jump" again, next branch tip (because this one is wired downward), then its base. Continue until the running pixel count matches what WLED reports. Save, then run approach C once in the dark to verify.

**Approach A and B details a developer needs.**

- Start the session with `ARWorldTrackingConfiguration`, `worldAlignment = .gravity` so y is up, and `sceneReconstruction = .mesh` when `ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)` is true.
- On tap, call `arView.raycast(from:allowing: .estimatedPlane, alignment: .any)` and take `.first`. The result's `worldTransform` is the waypoint position. Record `session.currentFrame.camera.trackingState` as the confidence.
- Show a small sphere at every waypoint and a line between consecutive ones so the user sees the path grow.
- Save `ARWorldMap` with the fixture so a later session can relocalise and add runs to the same frame.

**Approach C details.** Use the existing server to drive pixels over DDP (section 9). Detect blobs with Apple's Vision framework (`VNDetectContoursRequest` on a thresholded frame) or a simple brightest-pixel search. For 3D, either take two captures from known ARKit poses and triangulate, or capture from one pose and intersect each ray with the tapped model from A/B to refine it. Lighting pixels in binary code (bit 0 on frame 1, bit 1 on frame 2 …) identifies 500 pixels in 9 frames instead of 500.

## 8. Core algorithms

Six small algorithms turn waypoints into a pixel table. None needs a library beyond basic vector math; each is under a hundred lines.

**8.1 Arc length of a segment.** Every shape must answer "how long is this span?" because length ÷ pitch = pixel count.

- Straight: distance between the two points.
- Polyline: sum of the straight pieces.
- Helix with `n` turns, radius `r` and total rise `H`:

```latex
L = n \sqrt{(2\pi r)^2 + \left(\tfrac{H}{n}\right)^2}
```

- Curve: sample the spline at 100+ points and sum the chords. Good enough; nobody will see sub-millimetre error.

**8.2 Placing pixels along a path.** Walk the whole run in wire order with a running distance `d`, starting at 0 at the first pixel. Pixel `k` sits at `d = k × pitch`. For each pixel, find which segment contains that `d`, then evaluate that segment's position function at the local fraction `u = (d − segmentStart) ÷ segmentLength`. Jumps add to `d` but place no pixels, so `k` keeps counting from where it left off on the other side. This one loop handles every shape.

**8.3 Helix position function.** For parameter `u` from 0 to 1, with axis from point `A` to point `B`, radius `r(u)` (linear from start to end radius), `n` turns and a unit vector `e₁` perpendicular to the axis (`e₂ = axis × e₁`):

```latex
P(u) = A + u\,(B - A) + r(u)\,\big(\cos(2\pi n u)\, e_1 + \sin(2\pi n u)\, e_2\big)
```

Flip the sign of the sine term for the opposite handedness. The tapped points per turn are used to fit `r(u)` and to confirm `n`.

**8.4 Fitting a helix to taps.** The user taps once per turn, roughly on the same side of the trunk. Fit the axis as the line through the taps (least squares, or simply base-to-top for an upright tree). Each tap's distance from the axis is that turn's radius. The number of taps minus one is `n`. If LiDAR is on, refine each radius by fitting a circle to mesh vertices within ±5 cm of that height.

**8.5 Mesh snapping.** When a raycast hits the LiDAR mesh, the point is already on the surface. Without LiDAR, a tap may land on a plane behind the strip. Offer a "depth nudge" slider that moves the point along the camera ray so the user can bring it forward onto the strip while watching the overlay.

**8.6 Coordinate normalisation.** ARKit's origin is wherever the phone was at session start. Before saving, translate so the fixture's lowest pixel is at `y = 0` and the centroid is at `x = z = 0`, and rotate about `y` so the first run's start faces `+z`. Store the transform with the fixture so later sessions can relocalise. Effects then address the fixture in metres with a predictable frame: height is `y`, angle around is `atan2(x, z)`.

**8.7 Validation against WLED.** After placing pixels, compare the computed count per run with `pixelCount` from the controller. Show the difference live during capture ("318 placed / 300 on controller"). A mismatch over ±3 % means the pitch is wrong, a jump was missed, or a segment is the wrong shape. Let the user pick which to fix: scale the pitch to force the count, or add a `pixelCountOverride` on one segment.

**8.8 Pixel detection (approach C).** Threshold the frame, find the brightest connected blob, take its centroid. With ARKit's camera intrinsics and pose, each centroid gives a 3D ray. Intersect the ray with the current model's nearest segment to correct that pixel's position, or triangulate two rays from two poses for a model-free position.

## 9. Outputs

The pixel table feeds four consumers. Build them in the order below; each is independent of the others.

**9.1 Pixel coordinate table.** The table from section 6, exported as CSV and JSON. This is the universal interchange format: anything that can read a list of `(index, x, y, z)` can use it, including Pixelblaze (which takes exactly a JSON array of `[x, y, z]` per pixel in its Mapper tab) and any custom effect code.

**9.2 WLED ledmap generation.** For a fixture on one controller, sort pixels by a chosen spatial key and write a `ledmap.json`. The file is a `map` array where the position in the array is the new (spatial) order and the value is the physical pixel index; -1 marks a gap. Two useful maps:

- *Linear by height*: sort all pixels by `y`. A WLED 1D effect now runs bottom-to-top across every branch at once. This alone fixes the tree.
- *2D grid*: quantise `(angle, height)` or `(x, y)` into a grid of `width × height` cells, place each pixel's index in its cell, fill empty cells with -1, and write `width` and `height` into the file. WLED's 2D effects then treat the trunk as an unwrapped rectangle or the wall as a matrix.

Upload via WLED's `/edit` endpoint as `ledmap1.json` … `ledmap9.json`; a preset selects one with `"ledmap": 1`. The controller needs a reboot after upload. The existing server already talks to every controller, so it does the upload.

**9.3 Live spatial effects (multi-controller).** For fixtures spanning controllers, WLED cannot coordinate; the server renders instead. Each frame: for every pixel, compute a colour from its `(x, y, z)` and the current time (a bottom-to-top fill is `on if y < t × height`), group colours by controller and output, and send one DDP packet per controller. DDP is UDP on port 4048 with a 10-byte header: flags, sequence, data type, destination id, 4-byte data offset, 2-byte length, then raw RGB bytes. Up to 480 pixels (1440 bytes) per packet; larger outputs are split by offset. WLED ignores the optional timecode, so leave it out. Target 30–40 frames per second; a 500-pixel fixture is about 60 KB/s. WLED shows the stream as "realtime" and returns to its own effects after a timeout when the stream stops.

**9.4 xLights export.** xLights represents a fixture as a *model*. Two export paths:

- *Custom Model (.xmodel)*: an XML file whose `CustomModel` attribute is a grid of pixel numbers (columns separated by commas, rows by semicolons, blank cells empty), with `parm1` = width and `parm2` = height; a `Depth` value plus extra grid layers makes it 3D. Quantise the pixel table into that grid the same way as the 2D ledmap and write the numbers (xLights numbers pixels from 1, not 0). Set `StringType`, `PixelCount` and the controller/start-channel fields so xLights can drive the real strip over DDP or E1.31.
- *Poly Line model*: for polyline-shaped runs, emit the waypoints as the Poly Line's segment points. Simpler, but only for straight-piece shapes.

Open one exported `.xmodel` from the auschristmaslighting.com library in a text editor before writing the exporter; the format is small and the real files are the best specification.

**9.5 Live-check during capture.** Not an export, but the same plumbing: while the user places waypoint `k`, the server lights pixel `k` white over DDP so they can see they are tapping the right spot. Cheap to build once 9.3 exists, and it catches most capture mistakes immediately.

**9.6 xLights import.** The reverse of 9.4. Read a `.xmodel`, or the models in the show's `xlights_rgbeffects.xml`, turn each model's node grid or Poly Line points into Waypoints and Segments, and attach its controller and start channel to a Run. xLights positions are in layout units, so the importer asks for one real-world scale (metres per unit) and then applies the normalisation from 8.6. Import creates a new Fixture or proposes changes to an existing one; it never silently overwrites a phone capture.

**9.7 Wiring details in every export.** Both exporters write the controller block from the fleet's registry: controller name, IP, protocol (DDP), output, start channel and pixel count. If xLights does not yet know the controller, the sync adds it to the show's controller list (`xlights_networks.xml`) too. The fleet is the only place this information is ever typed. When a board is replaced, re-running the sync updates xLights.

## 10. System architecture

Three tiers. The phone only captures and previews; the server owns the model, the math and all talking to controllers; WLED is not modified at all.

&#91;embedded content: system architecture · one shared map, one sender per board\]

Solid boxes are new work; dashed boxes already exist or belong to someone else. The app never talks to a controller directly, so every controller-side detail (IPs, outputs, firmware quirks) stays in the one place that already handles it.

**Why the server, not the phone, renders effects.** Streaming 30+ frames a second to several controllers must run continuously, survive the phone locking, and stay on the home network. The existing Docker service on TrueNAS is already there, already discovers every board, and already calls the WLED API. Adding a renderer and a DDP sender to it is a small step; building the same thing to run reliably in an iOS background task is not.

**One sender at a time.** xLights already streams DDP to every board in this setup, and the fleet's renderer will too, so each board needs exactly one owner at any moment. The server keeps an `owner` per controller: `fleet`, `xlights` or `none`. Ownership changes in one of three ways, in order of preference: xSchedule tells the server when a playlist starts and stops (its REST API and plugin hooks allow this); the phone tells the server because it started the show; or the server notices a board has entered WLED's realtime mode from another source (`/json/info` reports `live` and the sender's IP in `lip`) and infers a show. While `owner = xlights`, the renderer skips that board, the app shows it as "show running", and ledmap pushes and reboots are blocked. When ownership returns to the fleet, it restores the board's previous look.

**Interfaces to define.**

| Interface | Direction | Shape |
| --- | --- | --- |
| Fixture CRUD | app ↔ server | `GET/PUT /fixtures/{id}` with the JSON from section 6. |
| Controller list | server → app | `GET /controllers` → id, name, IP, outputs with pixel counts, current owner (already exists in some form). |
| Pixel map | server → app | `GET /fixtures/{id}/pixelmap` → CSV or JSON rows. Server computes; app only displays. |
| Light one pixel | app → server | `POST /fixtures/{id}/highlight {controller, output, index}` → server sends a DDP frame. Used during capture. |
| Run effect | app → server | `POST /fixtures/{id}/effect {name, params}`; server streams until told to stop, skipping boards it does not own. |
| Push ledmap | app → server | `POST /fixtures/{id}/ledmap {slot, mode}` → server generates, uploads via WLED `/edit`, reboots. Refused while a show owns the board. |
| Export | app → server | `GET /fixtures/{id}/export?format=xmodel` → file with wiring details from the registry. |
| Import | app → server | `POST /fixtures/import {source, scaleMetresPerUnit}` with an `.xmodel` or `xlights_rgbeffects.xml`. |
| Controller sync | server → xLights | `POST /xlights/sync-controllers` writes or updates xLights controller entries from the registry. |
| Ownership | xSchedule or app → server | `POST /controllers/{id}/owner {owner}`; server pauses or resumes its effects and updates status. |
| Show control | app → server → xSchedule | `POST /shows/{playlist}/start` and `/stop`; server calls xSchedule's API and sets ownership for the show's boards. |

## 11. Technology choices

Native Swift on the phone, the existing server language on the back end, standard protocols between them. Nothing exotic is needed; every piece below is well documented and has sample code.

| Layer | Choice | Why | Alternative considered |
| --- | --- | --- | --- |
| App language / UI | Swift + SwiftUI | ARKit and RealityKit are Apple-only frameworks; SwiftUI gives the native feel the current web app lacks. | React Native / Flutter: AR bridges exist but lag Apple's releases and complicate LiDAR access. |
| AR tracking | ARKit `ARWorldTrackingConfiguration` | Position tracking, gravity alignment, raycasting, world-map save/restore. iOS 13.4+ for mesh. | None realistic on iOS. |
| Surface mesh | ARKit `sceneReconstruction = .mesh` (LiDAR devices) | Lets taps land on curved surfaces; gives trunk radius. Feature-detect at runtime and fall back to plane raycasts. | RoomPlan: built for rooms, not outdoor trees. |
| AR view and overlay | RealityKit `ARView` | Draw waypoint spheres and path lines in the camera view; one API for AR and the preview. | SceneKit `ARSCNView`: older, still works, more sample code online. Either is fine. |
| 3D preview | RealityKit (non-AR `ARView` with `cameraMode = .nonAR`) or SceneKit | Rotate and inspect the fixture without the camera. Colour pixels live from the pixel map. | Metal directly: overkill. |
| Pixel detection | Vision framework (`VNDetectContoursRequest`) or manual brightest-blob scan on `CVPixelBuffer` | On-device, fast, no OpenCV dependency. | OpenCV via Swift wrapper: more features, heavier build. |
| Persistence (phone) | JSON files in app documents + sync to server; Codable structs | Fixtures are small; no database needed. | Core Data / SwiftData: unnecessary for a few KB. |
| Server | Extend the existing Docker service in its current language | It already owns controller discovery and the WLED API. | A second service: more moving parts for no gain. |
| App ↔ server | HTTPS REST + one WebSocket for live highlight / effect status | Simple, debuggable with curl. | gRPC / MQTT: not needed at this scale. |
| Server → WLED (stream) | DDP over UDP, port 4048 | Lowest overhead, no universe arithmetic, WLED-native. Reference implementations exist in Python (LedFx), Rust (ddp-rs), Go, TypeScript. | E1.31/sACN: works, but 170-pixel universes make multi-output mapping fiddly. Art-Net: same. |
| Server → WLED (config) | WLED JSON API (`/json/info`, `/json/cfg`, `/json/state`) and `/edit` file upload | Already in use by the existing server. | — |
| Math | Plain `simd` types in Swift; NumPy or equivalent on the server | Vector math, least squares for axis fit, spline sampling. | A full geometry library is not needed. |
| xLights export | Hand-written XML writer | The `.xmodel` format is a few attributes on one element. | — |

**Minimum devices.** Any iPhone running iOS 17 for approach A. iPhone 12 Pro or later Pro models, or iPad Pro 2020+, for LiDAR mesh snapping. Test on one non-LiDAR phone early so the fallback path stays honest.

**Repository layout.** One repo for the iOS app, one for the server changes. Share the Fixture JSON schema as a versioned file both repos validate against, so a change to the data model is made in one place.

## 12. Phased build plan

Seven phases, each ending in a demo on real lights. Phase 1 is the first thing the user can hold; Phase 2 is the first thing that solves the tree.

&#91;embedded content: build plan · 7 phases, each gated by a live demo\]

Phase 0 (server) and Phase 1 (app) can be built in parallel by two people, or in that order by one. Nothing after Phase 2 is required for the core promise; Phases 3 to 6 each add a capture shortcut, an xLights integration or a verification step, and their order can change with what the user needs next.

**Definition of done for every phase.** The gate passes on real hardware, not a simulator. The Fixture JSON for the test fixture is checked into the repo as a regression fixture. A five-minute screen recording of the demo exists.

**Test fixtures to keep on hand.** One straight 1 m strip (sanity), one wall outline on a single output (Phase 1), and the branching tree on two outputs (Phase 2 onward). Keep them wired the same way across phases so results are comparable.

## 13. Risks and accuracy limits

The biggest risk is not the code; it is expecting centimetre precision from a phone. Design for "good enough that effects look right", which for a 60/m strip means roughly ±2 pixels, and verify with pixel detection where it matters.

| Risk | Likelihood | Effect | Mitigation |
| --- | --- | --- | --- |
| **Two senders at once** (fleet renderer and xLights both stream DDP) | High without rule 3 | Flicker, fighting colours, hard-to-explain failures during shows. | Owner flag per controller; the renderer refuses to send to boards it does not own; status visible in the app. |
| **xLights config drift** (today: 3 placeholder controllers, 6 models without a controller) | High | Shows drive the wrong port, or nothing. | Controller sync from the fleet on every export; a nightly check that diffs xLights controllers against the registry. |
| **ARKit drift** over a long walk around a large fixture | High | Waypoints at the end of the path are offset a few cm from the start. | Keep sessions short; save and relocalise with `ARWorldMap`; place a reference tap at the start that the user re-taps at the end so the app can measure and distribute the error. |
| **Taps miss thin branches** (no LiDAR, or LiDAR mesh too coarse) | High on non-Pro phones | Point lands on the wall or ground behind. | Depth-nudge slider; snap to the nearest point on the already-fitted segment; prefer LiDAR devices for trees. |
| **Pixel count mismatch** between model and controller | Medium | Effects appear shifted or truncated on one branch. | Live count readout during capture; per-segment override; final scale-to-fit option; pixel detection as the authoritative check. |
| **Outdoor lighting and weather** | Medium | ARKit tracking degrades in low light; LiDAR struggles with wet or glossy bark. | Capture in daylight or dusk; pixel detection runs at night when strips are visible, so the two approaches cover each other. |
| **DDP streaming stutters** on Wi-Fi with several boards | Medium | Visible flicker or lag during spatial effects. | Keep frame rate at 30; send one packet per output; put controllers on 2.4 GHz with a dedicated SSID; let WLED fall back to its own effect if packets stop. |
| **Ledmap limits in WLED** (1D only on older builds, reboot needed, 2D needs `width`/`height`) | Low | Single-controller export does not take effect. | Target WLED 0.14+ ; always write `width` and `height`; have the server reboot the board and confirm via `/json/info`. |
| **xLights format changes** (2026 release rewrote model serialisation) | Low | Exported `.xmodel` fails to import. | Export the simplest Custom Model shape; keep two real exported files from the current xLights as fixtures and diff against them. |
| **Scope creep into a sequencer** | Medium | Months spent on effect editing instead of mapping. | Ship three effects, then export to xLights for anything fancier. The app's job is the map. |
| **Multi-session alignment** when a fixture is captured over several days | Medium | Runs from different sessions sit in different coordinate frames. | Store the `ARWorldMap`; require re-tapping two labelled reference waypoints before adding runs; compute the rigid transform between them. |

**What accuracy to expect.** ARKit without LiDAR places a point to within about 1–3 cm at arm's length in good light; LiDAR brings surface placement to about 1 cm but the mesh itself is coarser than that on thin objects. Pixel detection is limited by camera resolution and distance: at 3 m with a 12 MP camera a pixel is a few image-pixels wide, which triangulates to under 1 cm. None of this matters much for a 16.7 mm pixel pitch, which is why the plan treats detection as verification rather than the primary capture.

## 14. Resources

Everything below was checked while writing this spec. Read the first group before Phase 1, the second before Phase 2, the third before Phase 4, and the fourth when planning Phase 5.

**ARKit and measuring (Phases 1, 3)**

- [ARMeshAnchor](https://developer.apple.com/documentation/arkit/armeshanchor) — Apple docs for the LiDAR scene mesh. Available iOS 13.4+.
- [ARRaycastQuery.Target.estimatedPlane](https://developer.apple.com/documentation/arkit/arraycastquery/target-swift.enum/estimatedplane) — explains that with `sceneReconstruction` on, raycasts hit the mesh, including curved or featureless surfaces.
- [Measuring objects with ARKit and RealityKit (Slalom Build)](https://medium.com/slalom-build/ios-16-how-arkit-and-realitykit-help-measure-objects-accurately-9128f4ca57a0) — a worked example with source code: place two points on the mesh, measure between them. Closest existing thing to the capture screen.
- [Place an anchor on the LiDAR mesh (Stack Overflow)](https://kaweah.freedombox.rocks/kiwix/content/stackoverflow.com_en_all_2023-11/questions/68388546/how-to-put-an-anchor-on-reconstructed-mesh-to-get-accuracy) — the exact `raycast(from:allowing:alignment:)` call to use on tap.
- [Building point clouds with ARKit and LiDAR](https://rss.boorghani.com/?p=246688) — if the team ever wants the raw depth data rather than the mesh.

**WLED protocols and mapping (Phases 2, 4)**

- [WLED Knowledge Base — Mapping](https://kno.wled.ge/advanced/mapping/) — ledmap.json format with 1D and 2D examples, `-1` gaps, `width`/`height`.
- [WLED MoonModules — Mapping](https://mm.kno.wled.ge/advanced/mapping/) — version differences, multiple ledmaps per controller, selecting a map from a preset.
- [WLED Knowledge Base — DDP](https://kno.wled.ge/interfaces/ddp/) — confirms port 4048 and that WLED ignores DDP timecodes; links to reference senders.
- [ddp-rs](https://github.com/coral/ddp-rs) — a clean, small DDP sender in Rust. Read `connection.rs` for the header layout and 480-pixel packet chunking, then port it to the server's language.
- [WLED Protocol Shim](https://github.com/lydonator/WLED_Protocol_Shim_ESP32C6) — a readable table of the DDP header bytes and of WLED's own UDP realtime protocols, useful if DDP ever needs a fallback.
- [Adafruit — LED mapping with WLED](https://learn.adafruit.com/led-festival-coat-with-mapping-and-wled/led-mapping) — a friendly walkthrough of building a ledmap for an irregular shape with gaps; good onboarding for whoever writes the generator.

**xLights (Phase 4)**

- [xLights manual — Custom Model](https://manual.xlights.org/xlights/chapters/chapter-four-tabs/models/custom-model) — what a Custom Model is, the grid editor, export to `.xmodel`, and the `Depth` setting for 3D.
- [xLights manual — PolyLine Model](https://manual.xlights.org/xlights/chapters/chapter-four-tabs/models/polyline-model) — the alternative export for straight-piece runs.
- [xLights manual — Models overview](https://manual.xlights.org/xlights/chapters/chapter-four-tabs/models) — model attributes, start channels, how models are stored in `xlights_rgbeffects.xml`.
- [xLights custom model library (auschristmaslighting.com)](https://auschristmaslighting.com/wiki/xLights-custom-models) — download a few real `.xmodel` files and use them as the format specification.
- [xLights 2026.04 release notes](https://auschristmaslighting.com/threads/2026-04-released.17353) — models are now native objects with a new serialiser; test the exporter against this version or later.

**Camera pixel detection (Phase 5)**

- [MariMapper](https://github.com/TheMariday/MariMapper) — open-source webcam-to-3D LED mapper. Lights one LED at a time, finds it in the frame, reconstructs 3D from several views, exports to Pixelblaze. The best reference for the detection loop and for what a 3D map file looks like.
- [pixel\_mapper (aaknitt)](https://github.com/aaknitt/pixel_mapper) — three-camera triangulation of a pixel-wrapped tree, output directly as an xLights 3D model. Essentially Phase 5 plus Phase 4 done once in Python.
- [LEDmap (devicerCode)](https://github.com/devicerCode/LEDmap) — a minimal 2D webcam mapper; small enough to read in an afternoon.
- [Hackaday — Computer vision maps Christmas lights](https://hackaday.com/2020/11/03/computer-vision-maps-christmas-lights) — context and discussion, including the note that Twinkly's commercial app does the same thing in-phone.

**Spatial effect design (Phase 2)**

- [Pixelblaze pixel mapping](https://docsearch.algolia.com/mcp/docs/repo/simap/pixelblaze) — the `render3D(index, x, y, z)` model: a per-pixel function of position and time. Copy this programming model for the server's renderer; it is the simplest way to write spatial effects.
- [LEDController mapping concepts (Finndersen)](https://github.com/Finndersen/LEDController/wiki/Reference) — "spatial strip segments" and "linear-to-spatial mappers", a tidy vocabulary for projecting 1D effects along a 3D direction.

**Show playback and ownership (Phase 5)**

- [xSchedule overview (xLightsSequencer)](https://tomevault.io/tome/xLightsSequencer/xSchedule) — xSchedule plays FSEQ sequences, manages playlists, outputs over E1.31, Art-Net and DDP, and includes a web UI, a plugin system and a REST API. That API is the hook for start/stop and ownership. Linux and Windows only.
- [xSchedule on Mac is deprecated (auschristmaslighting)](https://auschristmaslighting.com/threads/unable-to-schedule-in-xlights-scheduler.17179/latest) — community advice is to run xSchedule on Windows or Linux, or use FPP on a Raspberry Pi.
- This show's xLights configuration, read on 2026-11-08: 5 WLED controllers on DDP (Dig-Quad-V3, Dig-Uno-V3, WLED-Gledopto, Eaves at 780 channels, Front Door at 900 channels); 12 models, of which 6 have no controller and several Poly Lines have a pixel count of 0.

## 15. Open questions

Decide these before the matching phase starts. None blocks Phase 0 or Phase 1.

- [ ] **Server language.** The existing Docker service's language decides how the DDP sender and compiler are written. Confirm it, and whether it already exposes a REST API the app can extend.
- [x] **How does the fleet learn a show is running?** xSchedule is primary: its hook and the phone's start/stop. The fallback is WLED's realtime flag (`live` true and `lip` not the fleet server). Decided for Phase 5.
- [x] **xSchedule or FPP?** xSchedule. FPP stays behind the same player interface and is not implemented.
- [x] **Who fixes today's xLights gaps?** The fleet sync. It previews the diff, and writes only with `apply: true` after a timestamped backup. It fills channel count 1 and assigns a controller only when the match is unambiguous.
- [ ] **Which phone will do the capturing?** LiDAR (Pro) or not. This decides whether Phase 3 mesh snapping is on the critical path or a nice-to-have.
- [ ] **One fixture or many per session?** If the whole property is one "scene" (so a wave can run from the porch across the roofline to the tree), fixtures need a shared outdoor coordinate frame, which is harder than per-fixture frames. Suggest per-fixture first.
- [ ] **How exact must the tree be?** If ±2 pixels is fine, tap-to-place alone may be enough and Phase 6 can wait. If every pixel must be right, Phase 6 moves earlier.
- [ ] **Should ledmaps be pushed automatically?** Uploading and rebooting a controller is disruptive. Decide whether the app does it on save or the user presses a button.
- [ ] **Effect ownership.** Should the fleet's spatial effects appear inside WLED's UI (as a preset that switches to realtime) or only in the app? The first is friendlier for household members who already use WLED.
- [ ] **xLights depth.** Is a 2D Custom Model (unwrapped tree) enough for xLights, or is a true 3D model wanted? 3D is more work in the exporter and in xLights itself.
- [ ] **Name.** The project needs one before the repo is created.
