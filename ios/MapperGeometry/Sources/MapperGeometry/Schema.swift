import CoreFoundation
import Foundation

/// Checks fixture documents against `schema/fixture.v1.json`.
/// schemaVersion 1 and 2 are both valid. Version 2 adds groups, reference marks, and session alignments. A version 1 document stays valid.
public enum SchemaCheck {
    public static let schemaVersions = [1, 2]

    public static func errors(in value: Any, schema: [String: Any]) -> [String] {
        let defs = schema["$defs"] as? [String: Any] ?? [:]
        return check(value, schema, defs, "$")
    }

    public static func validateFixture(_ data: Data, schemaData: Data) throws -> [String] {
        let fixture = try JSONSerialization.jsonObject(with: data)
        let schema = try JSONSerialization.jsonObject(with: schemaData) as? [String: Any] ?? [:]
        return errors(in: fixture, schema: schema)
    }
}

private func check(_ instance: Any, _ schema: [String: Any], _ defs: [String: Any], _ path: String) -> [String] {
    if let ref = schema["$ref"] as? String {
        let name = ref.split(separator: "/").last.map(String.init) ?? ""
        guard let target = defs[name] as? [String: Any] else { return ["\(path) references unknown \(ref)"] }
        return check(instance, target, defs, path)
    }
    var errors: [String] = []
    if let constant = schema["const"], !same(instance, constant) {
        errors.append("\(path) must be \(constant)")
    }
    if let choices = schema["enum"] as? [Any], !choices.contains(where: { same(instance, $0) }) {
        errors.append("\(path) must be one of \(choices)")
    }
    if let expected = schema["type"] {
        let names = expected as? [String] ?? [expected as? String].compactMap { $0 }
        if !names.contains(where: { typeOk(instance, $0) }) {
            errors.append("\(path) has the wrong type")
            return errors
        }
    }
    if let text = instance as? String {
        if let minLength = schema["minLength"] as? Int, text.count < minLength { errors.append("\(path) is too short") }
        if let maxLength = schema["maxLength"] as? Int, text.count > maxLength { errors.append("\(path) is too long") }
    }
    if let number = numberValue(instance) {
        if let minimum = schema["minimum"] as? Double, number < minimum { errors.append("\(path) is below \(minimum)") }
        if let minimum = schema["minimum"] as? Int, number < Double(minimum) { errors.append("\(path) is below \(minimum)") }
        if let maximum = schema["maximum"] as? Double, number > maximum { errors.append("\(path) is above \(maximum)") }
        if let maximum = schema["maximum"] as? Int, number > Double(maximum) { errors.append("\(path) is above \(maximum)") }
        if let exclusive = numeric(schema["exclusiveMinimum"]), number <= exclusive {
            errors.append("\(path) must be greater than \(exclusive)")
        }
    }
    if let object = instance as? [String: Any], schema["properties"] != nil || (schema["type"] as? String) == "object" {
        let properties = schema["properties"] as? [String: Any] ?? [:]
        let required = schema["required"] as? [String] ?? []
        for key in required where object[key] == nil {
            errors.append("\(path).\(key) is required")
        }
        let allowExtra = schema["additionalProperties"] as? Bool ?? true
        for (key, value) in object {
            if let child = properties[key] as? [String: Any] {
                errors.append(contentsOf: check(value, child, defs, "\(path).\(key)"))
            } else if allowExtra == false {
                errors.append("\(path).\(key) is not allowed")
            }
        }
    }
    if let list = instance as? [Any], schema["items"] != nil || (schema["type"] as? String) == "array" {
        if let minItems = schema["minItems"] as? Int, list.count < minItems {
            errors.append("\(path) needs at least \(minItems) items")
        }
        if let maxItems = schema["maxItems"] as? Int, list.count > maxItems {
            errors.append("\(path) has too many items")
        }
        if let item = schema["items"] as? [String: Any] {
            for (index, value) in list.enumerated() {
                errors.append(contentsOf: check(value, item, defs, "\(path)[\(index)]"))
            }
        }
    }
    return errors
}

private func numeric(_ value: Any?) -> Double? {
    if let number = value as? Double { return number }
    if let number = value as? Int { return Double(number) }
    if let number = value as? NSNumber, !valueIsBool(value) { return number.doubleValue }
    return nil
}

private func numberValue(_ value: Any) -> Double? {
    if valueIsBool(value) { return nil }
    return numeric(value)
}

private func valueIsBool(_ value: Any?) -> Bool {
    // Linux bridges NSNumber 0 and 1 as Bool. Only a real JSON boolean is CFBoolean.
    guard let value, let number = value as? NSNumber else { return false }
    return CFGetTypeID(number) == CFBooleanGetTypeID()
}

private func typeOk(_ value: Any, _ name: String) -> Bool {
    switch name {
    case "object": return value is [String: Any]
    case "array": return value is [Any]
    case "string": return value is String
    case "boolean": return value is Bool
    case "null": return value is NSNull
    case "integer":
        if valueIsBool(value) { return false }
        if value is Int { return true }
        if let number = value as? Double { return number.rounded() == number }
        if let number = value as? NSNumber { return floor(number.doubleValue) == number.doubleValue }
        return false
    case "number":
        return numberValue(value) != nil
    default:
        return false
    }
}

private func same(_ lhs: Any, _ rhs: Any) -> Bool {
    if valueIsBool(lhs) || valueIsBool(rhs) {
        return (lhs as? Bool) == (rhs as? Bool) && valueIsBool(lhs) && valueIsBool(rhs)
    }
    if let left = numeric(lhs), let right = numeric(rhs), !(lhs is String), !(rhs is String) {
        return left == right
    }
    if let left = lhs as? String, let right = rhs as? String { return left == right }
    if lhs is NSNull && rhs is NSNull { return true }
    return false
}
