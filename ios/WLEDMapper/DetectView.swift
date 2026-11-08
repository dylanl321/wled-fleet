import ARKit
import CoreVideo
import MapperGeometry
import RealityKit
import SwiftUI
import UIKit

struct DetectScreen: View {
    @EnvironmentObject private var model: AppModel
    var fixtureId: String
    var previewOnly = false
    @StateObject private var camera = DetectionCamera()
    @State private var running = false
    @State private var status = "Darken the room. Hold the phone still."
    @State private var sightings: [Sighting] = []
    @State private var missing: [Int] = []
    @State private var pixelCount = 0
    @State private var accepting = false

    var body: some View {
        ZStack {
            Theme.background.ignoresSafeArea()
            VStack(alignment: .leading, spacing: 16) {
                Text("Find pixels")
                    .font(.largeTitle.weight(.semibold))
                    .foregroundStyle(Theme.ink)
                    .accessibilityIdentifier("screen.detect")
                Text(status)
                    .font(.body)
                    .foregroundStyle(Theme.muted)
                if !previewOnly {
                    DetectionPreview(camera: camera)
                        .frame(height: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 22, style: .continuous))
                }
                if pixelCount > 0 || previewOnly {
                    Text(previewOnly ? "60 pixels, 6 frames" : "\(pixelCount) pixels, \(sightings.count) found")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(Theme.ink)
                }
                if previewOnly || !missing.isEmpty {
                    Text(previewOnly ? "3 pixels were hidden. Walk to the other side and run it again." : "\(missing.count) pixels were hidden. Walk to the other side and run it again.")
                        .font(.body)
                        .foregroundStyle(Theme.warn)
                }
                Spacer()
                if !previewOnly {
                    BigButton(title: running ? "Looking" : (sightings.isEmpty ? "Start" : "From here"), prominent: sightings.isEmpty) {
                        Task { await run() }
                    }
                    .disabled(running)
                    if !sightings.isEmpty {
                        BigButton(title: accepting ? "Saving" : "Accept the corrections", prominent: true) {
                            Task { await accept() }
                        }
                        .disabled(accepting)
                    }
                } else {
                    BigButton(title: "Accept the corrections") {}
                        .accessibilityIdentifier("detect.accept")
                }
            }
            .padding(24)
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar(.hidden, for: .navigationBar)
    }

    private func run() async {
        guard !running else { return }
        running = true
        defer { running = false }
        guard let sample = camera.sample() else {
            status = "The camera is not tracking yet."
            Haptics.warning()
            return
        }
        let image = lumaImage(sample.buffer)
        if !isDark(image.average) {
            status = "The room is too bright. Turn the other lights off and try again."
            Haptics.warning()
            return
        }
        do {
            let plan = try await model.client.detectionPlan(fixture: fixtureId, mode: "binary", luma: image.average)
            pixelCount = plan.pixelCount
            var frames: [[ImageBlob]] = []
            for lit in plan.frames {
                try await model.client.showDetection(fixture: fixtureId, pixels: lit)
                try await Task.sleep(nanoseconds: 180_000_000)
                guard let frame = camera.sample() else { continue }
                let seen = lumaImage(frame.buffer)
                let contours = contourBlobs(frame.buffer)
                let blobs = contours.isEmpty ? brightestBlobs(seen.values, width: seen.width, height: seen.height, threshold: 180, limit: max(lit.count, 1)) : contours
                frames.append(blobs)
            }
            try await model.client.showDetection(fixture: fixtureId, pixels: [])
            let decoded = decodeStationary(frames)
            let pose = poseRows(sample.pose)
            let lens = intrinsics(of: sample.intrinsics)
            for sight in decoded {
                let ray = rayFromPixel(u: sight.u, v: sight.v, fx: lens.fx, fy: lens.fy, cx: lens.cx, cy: lens.cy, pose: pose)
                if let index = sightings.firstIndex(where: { $0.index == sight.index }) {
                    sightings[index].rays.append(ray)
                } else {
                    sightings.append(Sighting(index: sight.index, rays: [ray]))
                }
            }
            missing = missingIndices(plan.pixelCount, seen: Set(sightings.map(\.index)))
            status = missing.isEmpty ? "Every pixel was seen." : "Some pixels stayed dark from here."
            Haptics.success()
        } catch {
            status = error.localizedDescription
            Haptics.warning()
        }
    }

    private func accept() async {
        guard let fixture = model.fixture(fixtureId) else { return }
        accepting = true
        defer { accepting = false }
        let segments = (try? modelSegments(compile(fixture))) ?? []
        var corrections: [DetectionCorrection] = []
        for sight in sightings {
            let point: Vec3?
            if sight.rays.count >= 2 {
                let first = sight.rays[0]
                let second = sight.rays[1]
                point = triangulate(originA: first.origin, directionA: first.direction, originB: second.origin, directionB: second.direction)
            } else if let ray = sight.rays.first {
                point = refineWithModel(origin: ray.origin, direction: ray.direction, segments: segments)
            } else {
                point = nil
            }
            if let point {
                corrections.append(DetectionCorrection(ordinal: sight.index, position: [point.x, point.y, point.z]))
            }
        }
        do {
            let (saved, stillMissing) = try await model.client.acceptDetection(
                fixture: fixtureId,
                corrections: corrections,
                seen: sightings.map(\.index)
            )
            model.replace(saved)
            missing = stillMissing
            status = stillMissing.isEmpty ? "Corrections saved." : "Saved. The hidden pixels still need another viewpoint."
            Haptics.success()
            if stillMissing.isEmpty, !model.path.isEmpty { model.path.removeLast() }
        } catch {
            status = error.localizedDescription
            Haptics.warning()
        }
    }
}

private struct Sighting {
    var index: Int
    var rays: [(origin: Vec3, direction: Vec3)]
}

private func modelSegments(_ pixels: [MappedPixel]) -> [(Vec3, Vec3)] {
    var segments: [(Vec3, Vec3)] = []
    for pair in zip(pixels, pixels.dropFirst()) where pair.0.segment == pair.1.segment {
        segments.append((Vec3(pair.0.x, pair.0.y, pair.0.z), Vec3(pair.1.x, pair.1.y, pair.1.z)))
    }
    return segments
}

final class DetectionCamera: ObservableObject {
    weak var view: ARView?

    func sample() -> (buffer: CVPixelBuffer, intrinsics: simd_float3x3, pose: simd_float4x4)? {
        guard let frame = view?.session.currentFrame else { return nil }
        return (frame.capturedImage, frame.camera.intrinsics, frame.camera.transform)
    }
}

private struct DetectionPreview: UIViewRepresentable {
    var camera: DetectionCamera

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        camera.view = view
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        view.session.run(config)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
