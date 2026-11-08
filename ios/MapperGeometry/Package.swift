// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "MapperGeometry",
    products: [
        .library(name: "MapperGeometry", targets: ["MapperGeometry"])
    ],
    targets: [
        .target(name: "MapperGeometry"),
        .testTarget(name: "MapperGeometryTests", dependencies: ["MapperGeometry"])
    ]
)
