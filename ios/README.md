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
3. Aim the reticle and tap. Straight and Jump close after two points. Polyline, Helix, and Curve keep the points until you change shape. Helix asks for turns, radius, and which way it winds. **Nudge** slides the last point along the camera ray when a thin branch is hard to hit.
4. The count reads `placed · on controller`. Green means they agree within 3 percent. **Next** starts another output on the same fixture, which is how a tree spans two Dig-Unos.
5. While you tap, the server lights the pixel at the end of the wire over DDP so you can see you are on the right spot.
6. **Save** stores the fixture on the server and keeps an ARWorldMap on the phone so a later visit can line up with the same room. The map file stays on the phone. The server stores the JSON.

From the fixture screen you can orbit the pixels, run Fill, Rainbow, or Spin (the server streams them, and the preview plays the same effect), send a ledmap, or share CSV, JSON, Pixelblaze, or xLights. Sending a ledmap asks first. It reboots that one controller.

**Find every pixel** checks a saved fixture in the dark. The server turns the run's pixels off, then on, then in groups, and waits for the phone between each picture. The phone reads the bright spot, works out which pixel it was, and lays that ray onto the tapped path. Lights that were hidden get a second viewpoint. Dead pixels stay marked instead of being invented. Keep these writes a measured position per pixel. Replace the path saves detected waypoints instead. The details sheet switches to one pixel at a time, which is slower and easier to follow.

## Geometry package

`ios/MapperGeometry` is a Swift package with the same placement math as the Python server. It builds on Linux and on the phone.

```bash
cd ios/MapperGeometry
swift test
```

The shared document format is `schema/fixture.v1.json`. The three regression fixtures live in `fixtures/`.

## Simulator

The capture screen needs a real camera, so the simulator is only for the connect screen, the fixture list, and the 3D preview. The GitHub Action `iOS` generates the project, builds it for an iPhone simulator, runs the unit tests and the screenshot UI tests, and uploads the screenshots as artifacts.
