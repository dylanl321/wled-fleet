import CoreVideo
import Foundation
import MapperGeometry
import Vision
import simd

struct LumaImage {
    var values: [Double]
    var width: Int
    var height: Int
    var average: Double
}

func lumaImage(_ buffer: CVPixelBuffer) -> LumaImage {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
    let hasPlane = CVPixelBufferGetPlaneCount(buffer) > 0
    let width = hasPlane ? CVPixelBufferGetWidthOfPlane(buffer, 0) : CVPixelBufferGetWidth(buffer)
    let height = hasPlane ? CVPixelBufferGetHeightOfPlane(buffer, 0) : CVPixelBufferGetHeight(buffer)
    let base = hasPlane ? CVPixelBufferGetBaseAddressOfPlane(buffer, 0) : CVPixelBufferGetBaseAddress(buffer)
    let stride = hasPlane ? CVPixelBufferGetBytesPerRowOfPlane(buffer, 0) : CVPixelBufferGetBytesPerRow(buffer)
    guard let base, width > 0, height > 0 else { return LumaImage(values: [], width: 0, height: 0, average: 255) }
    let bytes = base.assumingMemoryBound(to: UInt8.self)
    var values: [Double] = []
    values.reserveCapacity(width * height)
    var total = 0.0
    for row in 0..<height {
        let line = bytes.advanced(by: row * stride)
        for column in 0..<width {
            let sample = Double(line[column])
            values.append(sample)
            total += sample
        }
    }
    return LumaImage(values: values, width: width, height: height, average: total / Double(values.count))
}

func contourBlobs(_ buffer: CVPixelBuffer) -> [ImageBlob] {
    let request = VNDetectContoursRequest()
    request.contrastAdjustment = 1.5
    request.detectsDarkOnLight = false
    let handler = VNImageRequestHandler(cvPixelBuffer: buffer, options: [:])
    guard (try? handler.perform([request])) != nil, let observation = request.results?.first as? VNContoursObservation else { return [] }
    let width = Double(CVPixelBufferGetWidth(buffer))
    let height = Double(CVPixelBufferGetHeight(buffer))
    var blobs: [ImageBlob] = []
    for index in 0..<observation.contourCount {
        guard let contour = try? observation.contour(at: index) else { continue }
        let points = contour.normalizedPoints
        if points.isEmpty { continue }
        var minX = 1.0
        var maxX = 0.0
        var minY = 1.0
        var maxY = 0.0
        for point in points {
            minX = min(minX, Double(point.x))
            maxX = max(maxX, Double(point.x))
            minY = min(minY, Double(point.y))
            maxY = max(maxY, Double(point.y))
        }
        if maxX - minX < 0.001 || maxY - minY < 0.001 { continue }
        blobs.append(ImageBlob((minX + maxX) / 2 * width, (1 - (minY + maxY) / 2) * height))
    }
    return blobs
}

func poseRows(_ transform: simd_float4x4) -> [[Double]] {
    (0..<4).map { row in
        (0..<4).map { column in Double(transform[column][row]) }
    }
}

func intrinsics(of matrix: simd_float3x3) -> (fx: Double, fy: Double, cx: Double, cy: Double) {
    (Double(matrix.columns.0.x), Double(matrix.columns.1.y), Double(matrix.columns.2.x), Double(matrix.columns.2.y))
}
