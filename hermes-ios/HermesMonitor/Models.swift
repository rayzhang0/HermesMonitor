import Foundation

struct InventoryPayload: Decodable {
    let generatedAt: String
    let lastCheckedAt: String?
    let available: [TrackedProduct]
    let history: [AvailabilityRecord]

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case lastCheckedAt = "last_checked_at"
        case available
        case history
    }
}

struct TrackedProduct: Identifiable, Decodable, Hashable {
    let productKey: String
    let name: String
    let url: String
    let price: String?
    let imageURL: String?
    let firstSeenAt: String?
    let lastSeenAt: String?
    let purchasableStatus: String?
    let purchasableCheckedAt: String?

    var id: String { productKey }

    enum CodingKeys: String, CodingKey {
        case productKey = "product_key"
        case name, url, price
        case imageURL = "image_url"
        case firstSeenAt = "first_seen_at"
        case lastSeenAt = "last_seen_at"
        case purchasableStatus = "purchasable_status"
        case purchasableCheckedAt = "purchasable_checked_at"
    }
}

struct AvailabilityRecord: Identifiable, Decodable, Hashable {
    let productKey: String
    let name: String
    let url: String
    let price: String?
    let imageURL: String?
    let purchasableStatus: String?
    let purchasableCheckedAt: String?
    let availableFrom: String
    let availableUntil: String?

    var id: String { productKey + availableFrom }
    var isActive: Bool { availableUntil == nil }

    enum CodingKeys: String, CodingKey {
        case productKey = "product_key"
        case name, url, price
        case imageURL = "image_url"
        case purchasableStatus = "purchasable_status"
        case purchasableCheckedAt = "purchasable_checked_at"
        case availableFrom = "available_from"
        case availableUntil = "available_until"
    }
}


struct ProductHistoryGroup: Identifiable, Hashable {
    let productKey: String
    let name: String
    let url: String
    let price: String?
    let imageURL: String?
    let records: [AvailabilityRecord]

    var id: String { productKey }
    var isCurrentlyVisible: Bool { records.contains { $0.isActive } }
    var latestRecord: AvailabilityRecord? { records.sorted { ($0.availableUntil ?? $0.availableFrom) > ($1.availableUntil ?? $1.availableFrom) }.first }
}
