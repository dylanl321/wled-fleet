import ARKit
import CoreVideo
import MapperGeometry
import RealityKit
import SwiftUI
import UIKit
import Vision

struct CapturedSight {
    var bytes: [UInt8]
    var width: Int
    var height: Int
    var scale: Int
    var pose: CameraPose
    var intrinsics: CameraIntrinsics
}

final class DetectCamera: ObservableObject {
    weak var view: ARView?
    private var started = false

    func start(_ view: ARView) {
        self.view = view
        guard !started else { return }
        started = true
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        config.planeDetection = []
        config.environmentTexturing = .none
        let formats = ARWorldTrackingConfiguration.supportedVideoFormats
        if let format = formats.min(by: { abs($0.imageResolution.width - 1920) < abs($1.imageResolution.width - 1920) }) {
            config.videoFormat = format
        }
        view.session.run(config, options: [.resetTracking, .removeExistingAnchors])
    }

    func grab() -> CapturedSight? {
        guard let frame = view?.session.currentFrame else { return nil }
        guard let luma = luminance(frame.capturedImage) else { return nil }
        let factor = downsampleFactor(width: luma.width, height: luma.height)
        let small = downsampleMax(luma.bytes, width: luma.width, height: luma.height, factor: factor)
        let transform = frame.camera.transform
        let pose = CameraPose(
            right: Vec3(Double(transform.columns.0.x), Double(transform.columns.0.y), Double(transform.columns.0.z)),
            up: Vec3(Double(transform.columns.1.x), Double(transform.columns.1.y), Double(transform.columns.1.z)),
            back: Vec3(Double(transform.columns.2.x), Double(transform.columns.2.y), Double(transform.columns.2.z)),
            origin: Vec3(Double(transform.columns.3.x), Double(transform.columns.3.y), Double(transform.columns.3.z))
        )
        let matrix = frame.camera.intrinsics
        let sx = luma.width == 0 ? 1 : Double(luma.width) / Double(CVPixelBufferGetWidth(frame.capturedImage))
        let sy = luma.height == 0 ? 1 : Double(luma.height) / Double(CVPixelBufferGetHeight(frame.capturedImage))
        let intrinsics = CameraIntrinsics(
            fx: Double(matrix.columns.0.x) * sx,
            fy: Double(matrix.columns.1.y) * sy,
            cx: Double(matrix.columns.2.x) * sx,
            cy: Double(matrix.columns.2.y) * sy
        )
        return CapturedSight(
            bytes: small.bytes,
            width: small.width,
            height: small.height,
            scale: factor,
            pose: pose,
            intrinsics: intrinsics
        )
    }

    func centroids(_ sight: CapturedSight, threshold: Int, vision: Bool) -> [ImagePoint] {
        if vision, let found = visionCentroids(sight, threshold: threshold), !found.isEmpty {
            return found
        }
        return detectBrightBlobs(
            luminance: sight.bytes,
            width: sight.width,
            height: sight.height,
            threshold: threshold
        ).map { fullResolution($0.centroid, scale: sight.scale) }
    }

    private func visionCentroids(_ sight: CapturedSight, threshold: Int) -> [ImagePoint]? {
        let cutoff = UInt8(min(max(threshold, 0), 255))
        var binary = sight.bytes
        for index in binary.indices {
            binary[index] = binary[index] >= cutoff ? 255 : 0
        }
        let data = Data(binary) as CFData
        guard let provider = CGDataProvider(data: data),
              let image = CGImage(
                width: sight.width,
                height: sight.height,
                bitsPerComponent: 8,
                bitsPerPixel: 8,
                bytesPerRow: sight.width,
                space: CGColorSpaceCreateDeviceGray(),
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                provider: provider,
                decode: nil,
                shouldInterpolate: false,
                intent: .defaultIntent
              ) else { return nil }
        let request = VNDetectContoursRequest()
        request.contrastAdjustment = 1
        request.detectsDarkOnLight = false
        request.maximumImageDimension = 512
        do {
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        } catch {
            return nil
        }
        guard let observation = request.results?.first as? VNContoursObservation else { return [] }
        var points: [ImagePoint] = []
        for index in 0..<observation.topLevelContourCount {
            guard let contour = try? observation.topLevelContour(at: index) else { continue }
            let samples = contour.normalizedPoints
            guard !samples.isEmpty else { continue }
            var sumX = 0.0
            var sumY = 0.0
            for sample in samples {
                sumX += Double(sample.x)
                sumY += Double(sample.y)
            }
            let count = Double(samples.count)
            let local = ImagePoint(
                x: sumX / count * Double(sight.width),
                y: (1 - sumY / count) * Double(sight.height)
            )
            points.append(fullResolution(local, scale: sight.scale))
        }
        return points
    }
}

private struct LumaPlane {
    var bytes: [UInt8]
    var width: Int
    var height: Int
}

private func luminance(_ buffer: CVPixelBuffer) -> LumaPlane? {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    if CVPixelBufferGetPlaneCount(buffer) > 0 {
        guard let base = CVPixelBufferGetBaseAddressOfPlane(buffer, 0) else { return nil }
        let width = CVPixelBufferGetWidthOfPlane(buffer, 0)
        let height = CVPixelBufferGetHeightOfPlane(buffer, 0)
        let stride = CVPixelBufferGetBytesPerRowOfPlane(buffer, 0)
        var bytes = [UInt8](repeating: 0, count: width * height)
        bytes.withUnsafeMutableBytes { raw in
            guard let destination = raw.baseAddress else { return }
            for row in 0..<height {
                destination.advanced(by: row * width).copyMemory(from: base.advanced(by: row * stride), byteCount: width)
            }
        }
        return LumaPlane(bytes: bytes, width: width, height: height)
    }
    guard let base = CVPixelBufferGetBaseAddress(buffer) else { return nil }
    let width = CVPixelBufferGetWidth(buffer)
    let height = CVPixelBufferGetHeight(buffer)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    var bytes = [UInt8](repeating: 0, count: width * height)
    let pixel = UnsafeRawPointer(base)
    for y in 0..<height {
        let row = pixel.advanced(by: y * stride)
        for x in 0..<width {
            let sample = row.advanced(by: x * 4).assumingMemoryBound(to: UInt8.self)
            let red = Int(sample[2])
            let green = Int(sample[1])
            let blue = Int(sample[0])
            bytes[y * width + x] = UInt8((red * 3 + green * 6 + blue) / 10)
        }
    }
    return LumaPlane(bytes: bytes, width: width, height: height)
}

struct FindCanvas: UIViewRepresentable {
    var camera: DetectCamera

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        view.automaticallyConfigureSession = false
        camera.start(view)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {}
}
