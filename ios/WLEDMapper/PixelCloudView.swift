import MapperGeometry
import SceneKit
import SwiftUI
import UIKit

struct PixelCloudView: UIViewRepresentable {
    var pixels: [MappedPixel]
    var colors: [RGB]

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = UIColor(red: 0.07, green: 0.07, blue: 0.08, alpha: 1)
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.antialiasingMode = .multisampling4X
        view.scene = SCNScene()
        return view
    }

    func updateUIView(_ view: SCNView, context: Context) {
        if context.coordinator.pixelCount != pixels.count {
            context.coordinator.rebuild(pixels, colors: colors, in: view)
        } else {
            context.coordinator.move(pixels)
            context.coordinator.paint(colors)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var pixelCount: Int { nodes.count }
        private var nodes: [SCNNode] = []

        func rebuild(_ pixels: [MappedPixel], colors: [RGB], in view: SCNView) {
            let scene = SCNScene()
            nodes = pixels.enumerated().map { index, pixel in
                let sphere = SCNSphere(radius: 0.012)
                sphere.segmentCount = 8
                let node = SCNNode(geometry: sphere)
                node.position = SCNVector3(pixel.x, pixel.y, pixel.z)
                paint(node, colors.indices.contains(index) ? colors[index] : RGB(255, 180, 60))
                scene.rootNode.addChildNode(node)
                return node
            }
            let camera = SCNNode()
            camera.camera = SCNCamera()
            camera.position = SCNVector3(1.4, 1.15, 1.8)
            camera.look(at: SCNVector3(0, 0.7, 0))
            scene.rootNode.addChildNode(camera)
            view.scene = scene
            view.pointOfView = camera
        }

        func move(_ pixels: [MappedPixel]) {
            for (node, pixel) in zip(nodes, pixels) {
                node.position = SCNVector3(pixel.x, pixel.y, pixel.z)
            }
        }

        func paint(_ colors: [RGB]) {
            for (node, color) in zip(nodes, colors) {
                paint(node, color)
            }
        }

        private func paint(_ node: SCNNode, _ color: RGB) {
            node.geometry?.firstMaterial?.diffuse.contents = UIColor(
                red: CGFloat(color.r) / 255,
                green: CGFloat(color.g) / 255,
                blue: CGFloat(color.b) / 255,
                alpha: 1
            )
            node.geometry?.firstMaterial?.lightingModel = .constant
        }
    }
}
