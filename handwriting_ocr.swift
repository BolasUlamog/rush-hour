import AppKit
import Foundation
import Vision

struct OCRLine: Codable {
    let text: String
    let confidence: Float
    let alternatives: [String]
    let y: CGFloat
}

struct RegionRequest: Codable {
    let id: String
    let rect: [CGFloat]        // x, y, width, height in pixels, origin top-left
    let alphabet: String?      // optional hint, e.g. "ABCDEFX" or "12345"
}

struct Glyph: Codable {
    let c: String
    let x: Double        // normalized to the requested region, origin top-left
    let y: Double
    let w: Double
    let h: Double
}

struct Candidate: Codable {
    let text: String
    let confidence: Float
    let obs: Int          // which recognized line this reading came from
    let glyphs: [Glyph]
}

struct RegionResult: Codable {
    let id: String
    let candidates: [Candidate]
}

struct BarcodeResult: Codable {
    let payload: String
    let symbology: String
    let x: Double        // normalized to the image, origin top-left
    let y: Double
    let w: Double
    let h: Double
}

/// A crop padded and scaled for Vision, plus what is needed to map recognized
/// boxes back onto the caller's region.
struct Prepared {
    let image: CGImage
    let padX: Int
    let padY: Int
    let drawW: Int
    let drawH: Int
}

func recognize(_ image: CGImage, correction: Bool = false) throws -> [(VNRecognizedTextObservation, [VNRecognizedText])] {
    var observations: [VNRecognizedTextObservation] = []
    var recognitionError: Error?
    let request = VNRecognizeTextRequest { request, error in
        recognitionError = error
        observations = request.results as? [VNRecognizedTextObservation] ?? []
    }
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = correction
    request.recognitionLanguages = ["en-US"]
    request.minimumTextHeight = 0.01
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    if let error = recognitionError { throw error }
    return observations.compactMap { observation in
        let candidates = observation.topCandidates(4)
        guard !candidates.isEmpty else { return nil }
        return (observation, candidates)
    }
}

func detectInkBands(_ image: CGImage) -> [(start: Int, end: Int)] {
    let width = image.width
    let height = image.height
    var pixels = [UInt8](repeating: 255, count: width * height)
    guard let context = CGContext(
        data: &pixels,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: width,
        space: CGColorSpaceCreateDeviceGray(),
        bitmapInfo: CGImageAlphaInfo.none.rawValue
    ) else { return [] }
    context.setFillColor(gray: 1, alpha: 1)
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))

    let minimumInk = max(2, width / 140)
    var activeRows: [Int] = []
    for y in 0..<height {
        var dark = 0
        for x in 0..<width where pixels[y * width + x] < 105 { dark += 1 }
        if dark >= minimumInk { activeRows.append(y) }
    }
    guard let first = activeRows.first else { return [] }
    let joinGap = max(2, height / 250)
    var bands: [(Int, Int)] = []
    var start = first
    var previous = first
    for row in activeRows.dropFirst() {
        if row - previous > joinGap {
            bands.append((start, previous))
            start = row
        }
        previous = row
    }
    bands.append((start, previous))
    let margin = max(5, height / 100)
    return bands
        .filter { $0.1 - $0.0 >= 3 }
        .map { (max(0, $0.0 - margin), min(height - 1, $0.1 + margin)) }
}

/// Vision needs surrounding whitespace and a reasonable pixel height to see
/// handwriting, and answer rows are small and tightly ruled, so every crop is
/// padded with white and scaled up before recognition.
func prepare(_ image: CGImage, minimumHeight: Int = 190) -> Prepared {
    let factor = max(1, Int(ceil(Double(minimumHeight) / Double(max(1, image.height)))))
    let drawW = image.width * factor
    let drawH = image.height * factor
    let padX = max(14, drawH / 2)
    let padY = max(14, drawH / 2)
    let width = drawW + padX * 2
    let height = drawH + padY * 2
    guard let context = CGContext(
        data: nil,
        width: width,
        height: height,
        bitsPerComponent: 8,
        bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
    ) else { return Prepared(image: image, padX: 0, padY: 0, drawW: image.width, drawH: image.height) }
    context.interpolationQuality = .high
    context.setFillColor(gray: 1, alpha: 1)
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    context.draw(image, in: CGRect(x: padX, y: padY, width: drawW, height: drawH))
    let output = context.makeImage() ?? image
    return Prepared(image: output, padX: padX, padY: padY, drawW: drawW, drawH: drawH)
}

/// Per-character boxes let the caller decide which printed column a character
/// was written in, which is far more reliable than recognizing each cell alone.
func glyphs(of text: VNRecognizedText, in prepared: Prepared) -> [Glyph] {
    let imageW = Double(prepared.drawW + prepared.padX * 2)
    let imageH = Double(prepared.drawH + prepared.padY * 2)
    var result: [Glyph] = []
    let string = text.string
    var index = string.startIndex
    while index < string.endIndex {
        let next = string.index(after: index)
        let range = index..<next
        let character = String(string[range])
        index = next
        if character.trimmingCharacters(in: .whitespaces).isEmpty { continue }
        guard let box = try? text.boundingBox(for: range) else { continue }
        let rect = box.boundingBox
        let left = rect.minX * imageW
        let right = rect.maxX * imageW
        let top = (1 - rect.maxY) * imageH
        let bottom = (1 - rect.minY) * imageH
        let x = (left - Double(prepared.padX)) / Double(prepared.drawW)
        let x2 = (right - Double(prepared.padX)) / Double(prepared.drawW)
        let y = (top - Double(prepared.padY)) / Double(prepared.drawH)
        let y2 = (bottom - Double(prepared.padY)) / Double(prepared.drawH)
        result.append(Glyph(c: character, x: x, y: y, w: max(0, x2 - x), h: max(0, y2 - y)))
    }
    return result
}

func readRegions(_ image: CGImage, _ regions: [RegionRequest]) -> [RegionResult] {
    regions.map { region in
        guard region.rect.count == 4 else { return RegionResult(id: region.id, candidates: []) }
        let rect = CGRect(x: region.rect[0], y: region.rect[1], width: region.rect[2], height: region.rect[3])
            .intersection(CGRect(x: 0, y: 0, width: image.width, height: image.height))
        guard !rect.isNull, rect.width >= 4, rect.height >= 4, let crop = image.cropping(to: rect) else {
            return RegionResult(id: region.id, candidates: [])
        }
        let prepared = prepare(crop)
        var candidates: [Candidate] = []
        var seen = Set<String>()
        var observation = 0
        // Two passes: language correction off keeps isolated characters honest,
        // on recovers words such as RIGHT written in loose handwriting. Readings
        // are capped per recognized line, never globally, so a faintly read line
        // is not crowded out by a confidently read one elsewhere on the image.
        for correction in [false, true] {
            guard let pieces = try? recognize(prepared.image, correction: correction), !pieces.isEmpty else { continue }
            for (_, texts) in pieces.sorted(by: { $0.0.boundingBox.minY > $1.0.boundingBox.minY }) {
                var kept = 0
                for text in texts {
                    if kept >= 4 { break }
                    let trimmed = text.string.trimmingCharacters(in: .whitespacesAndNewlines)
                    if trimmed.isEmpty { continue }
                    let key = "\(observation)|\(trimmed)"
                    if seen.contains(key) { continue }
                    seen.insert(key)
                    kept += 1
                    candidates.append(Candidate(text: trimmed, confidence: text.confidence, obs: observation,
                                                glyphs: glyphs(of: text, in: prepared)))
                }
                observation += 1
            }
        }
        return RegionResult(id: region.id, candidates: candidates)
    }
}

/// Read any QR codes in the image. A Gridlock sheet carries its puzzle in one,
/// and the code's position on the page also reveals which way up the photo is.
func readBarcodes(_ image: CGImage) -> [BarcodeResult] {
    var observations: [VNBarcodeObservation] = []
    let request = VNDetectBarcodesRequest { request, _ in
        observations = request.results as? [VNBarcodeObservation] ?? []
    }
    request.symbologies = [.qr]
    try? VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return observations.compactMap { observation in
        guard let payload = observation.payloadStringValue else { return nil }
        let box = observation.boundingBox        // normalized, origin bottom-left
        return BarcodeResult(
            payload: payload,
            symbology: observation.symbology.rawValue,
            x: Double(box.minX),
            y: Double(1 - box.maxY),
            w: Double(box.width),
            h: Double(box.height)
        )
    }
}

func loadImage(_ path: String) -> CGImage? {
    guard let image = NSImage(contentsOf: URL(fileURLWithPath: path)) else { return nil }
    return image.cgImage(forProposedRect: nil, context: nil, hints: nil)
}

func emit<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.withoutEscapingSlashes]
    print(String(data: try encoder.encode(value), encoding: .utf8)!)
}

let arguments = Array(CommandLine.arguments.dropFirst())
guard let imagePath = arguments.first else {
    FileHandle.standardError.write(Data("Usage: handwriting_ocr image-path [--regions regions.json | --barcodes]\n".utf8))
    exit(2)
}
guard let cgImage = loadImage(imagePath) else {
    FileHandle.standardError.write(Data("Could not open image.\n".utf8))
    exit(3)
}

do {
    if arguments.contains("--barcodes") {
        try emit(readBarcodes(cgImage))
        exit(0)
    }

    if let flag = arguments.firstIndex(of: "--regions"), arguments.count > flag + 1 {
        let data = try Data(contentsOf: URL(fileURLWithPath: arguments[flag + 1]))
        let regions = try JSONDecoder().decode([RegionRequest].self, from: data)
        try emit(readRegions(cgImage, regions))
        exit(0)
    }

    var lines: [OCRLine] = []
    let bands = detectInkBands(cgImage)
    for band in bands {
        let height = band.end - band.start + 1
        guard let crop = cgImage.cropping(to: CGRect(x: 0, y: band.start, width: cgImage.width, height: height)) else { continue }
        let pieces = try recognize(crop).sorted { $0.0.boundingBox.minX < $1.0.boundingBox.minX }
        guard !pieces.isEmpty else { continue }
        let bestParts = pieces.map { $0.1[0].string }
        let text = bestParts.joined(separator: " ")
        let confidence = pieces.map { $0.1[0].confidence }.reduce(0, +) / Float(pieces.count)
        var alternatives: [String] = []
        for (pieceIndex, piece) in pieces.enumerated() {
            for candidate in piece.1.dropFirst() {
                var parts = bestParts
                parts[pieceIndex] = candidate.string
                alternatives.append(parts.joined(separator: " "))
            }
        }
        lines.append(OCRLine(
            text: text,
            confidence: confidence,
            alternatives: alternatives,
            y: 1 - (CGFloat(band.start + band.end) / 2 / CGFloat(cgImage.height))
        ))
    }
    if lines.count < 2 {
        lines = try recognize(cgImage).map { observation, candidates in
            OCRLine(
                text: candidates[0].string,
                confidence: candidates[0].confidence,
                alternatives: candidates.dropFirst().map(\.string),
                y: observation.boundingBox.midY
            )
        }
    }
    lines.sort { $0.y > $1.y }
    try emit(lines)
} catch {
    FileHandle.standardError.write(Data("Recognition failed: \(error)\n".utf8))
    exit(4)
}
