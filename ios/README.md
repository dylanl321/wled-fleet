# WLED Mapper

WLED Mapper is the iPhone app for mapping a real LED installation. You walk the strip, tap points along the wire, and the phone builds a 3D picture of where every pixel is. The WLED Fleet server keeps that picture, lights individual pixels while you tap, runs spatial effects, and can hand a ledmap or an xLights model to the controller.

The app talks only to the fleet server. It never opens a connection to a WLED board.

## What you need

- A Mac with Xcode 15 or newer (the App Store build is fine).
- An iPhone running iOS 17 or newer. Any iPhone can place points on detected planes. iPhone 12 Pro and later Pro models, and iPad Pro from 2020, also have LiDAR, and the app uses the mesh when it is there.
- The fleet server on the same Wi-Fi. The preview image is `ghcr.io/dylanl321/wled-fleet:mapper`. On TrueNAS, change the image tag from `latest` to `mapper` and leave the data volume where it is. `:latest` is not moved by this branch.
- Your Apple ID. A free personal team is enough to run the app on your own phone for a week at a time. A paid developer account is only needed for TestFlight.

## Install it on your iPhone

1. Install Xcode from the Mac App Store and open it once so it can finish installing the command line tools.
2. Install XcodeGen, which regenerates the Xcode project from `project.yml`:

   ```bash
   brew install xcodegen
   ```

3. Clone this repo and check out the branch you want (`mapper` until it is merged, then `main`):

   ```bash
   git clone https://github.com/dylanl321/wled-fleet.git
   cd wled-fleet
   git checkout mapper
   ```

4. Generate the project and open it:

   ```bash
   cd ios
   xcodegen generate
   open WLEDMapper.xcodeproj
   ```

5. In Xcode, select the **WLEDMapper** target, then **Signing & Capabilities**. Check **Automatically manage signing**. Set **Team** to your personal team (your Apple ID) or your paid team. If Xcode says the bundle id `com.dylanl321.wledmapper` is not available to your team, change it to something like `com.yourname.wledmapper`. Leave the rest of the capabilities alone.

6. Plug in the iPhone, unlock it, and tap Trust if it asks. In Xcode's scheme menu, choose your iPhone instead of a simulator. Press Run.

7. The first launch from a free or new team fails until the phone trusts the developer. On the iPhone open **Settings → General → VPN & Device Management**, tap your Apple ID under Developer App, and tap Trust. Run again from Xcode.

8. The phone and the server have to be on the same network. The first screen looks for a Bonjour service named `_wledmapper._tcp` (the server advertises that while its normal discovery loop is on). If nothing appears, type the address yourself, for example `http://192.168.1.20:8787`. The app remembers it. If the server has `ADMIN_PASSWORD` set, the same screen asks for that password.

A free personal team provisioning profile expires after about 7 days. Plug the phone back into the Mac and press Run to refresh it. That is the only reason to use TestFlight later.

## TestFlight, later

TestFlight needs a paid Apple Developer account. In Xcode choose **Product → Archive**, then **Distribute App → App Store Connect**. Upload the build, and in App Store Connect add yourself as an internal tester. Nothing in this repo uploads a build automatically. The GitHub Action only builds and tests on the iOS Simulator.

## How a mapping session goes

1. Tap **Map a fixture**, name it, and pick Wall, Column, Tree, or Freeform.
2. Choose the controller output and the pitch (60 per metre is the default). The list comes from the fleet server, including each output's pixel count and color order.
3. Aim the reticle and tap. Straight and Jump close after two points. Polyline, Helix, and Curve keep the points until you change shape. A helix is one tap per turn, on the same side of the trunk; the radius and the wind direction sit on that panel. **Nudge** slides the last point along the camera ray when a thin branch is hard to hit. On a tree, **Trunk** and **New branch** name the limb you are on.
4. The count reads `placed · on controller`. Green means they agree within 3 percent. **Next** starts another output on the same fixture, including a second port on the same board.
5. While you tap, the server lights the pixel at the end of the wire over DDP so you can see you are on the right spot.
6. **Close loop** asks you to tap the start again. The phone shows how far that re-tap missed, and **Before** / **After** show the path before you commit. **Straighten** shares the miss along the walk. **Undo** puts the taps back.
7. **Save** stores the fixture on the server and keeps an ARWorldMap on the phone so a later visit can line up with the same room. The map file stays on the phone. The server stores the JSON, including the labelled marks.

Open the same fixture later and the phone tries the saved scan first. If it finds the room, it says **Same place** and you can keep tapping. If it does not, it asks you to tap two of the saved marks (a third is there when you want the match to be stricter). It shows the miss in centimetres before you accept it.

From the fixture screen you can orbit the pixels, run Fill, Rainbow, or Spin. A tree also has **Chase**, which runs out from the trunk along every branch at once. The **All** menu limits an effect to the trunk, every branch, or one branch. The slider button hides **At a height** and **Along the branch**. The server streams the effect, and the preview plays the same one. You can send a ledmap, or share CSV, JSON, Pixelblaze, or xLights. Sending a ledmap asks first. It reboots that one controller.

## Geometry package

`ios/MapperGeometry` is a Swift package with the same placement math as the Python server. It builds on Linux and on the phone.

```bash
cd ios/MapperGeometry
swift test
```

The shared document format is `schema/fixture.v1.json`. The three regression fixtures live in `fixtures/`.

## Simulator

The capture screen needs a real camera, so the simulator is only for the connect screen, the fixture list, and the 3D preview. The GitHub Action `iOS` generates the project, builds it for an iPhone simulator, runs the unit tests and the screenshot UI tests, and uploads the screenshots as artifacts.
