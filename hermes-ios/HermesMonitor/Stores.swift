import Foundation

@MainActor
final class InventoryStore: ObservableObject {
    private static let defaultFeedURL = Bundle.main.object(forInfoDictionaryKey: "HermesFeedURL") as? String ?? ""

    @Published private(set) var payload: InventoryPayload?
    @Published var feedURL: String
    @Published private(set) var errorMessage: String?
    @Published private(set) var isLoading = false

    var available: [TrackedProduct] {
        (payload?.available ?? []).sorted { left, right in
            let leftVisibleSince = left.firstSeenAt ?? ""
            let rightVisibleSince = right.firstSeenAt ?? ""
            if leftVisibleSince != rightVisibleSince { return leftVisibleSince > rightVisibleSince }

            let leftRank = purchasableRank(left.purchasableStatus)
            let rightRank = purchasableRank(right.purchasableStatus)
            if leftRank != rightRank { return leftRank < rightRank }

            let leftPrice = priceNumber(left.price)
            let rightPrice = priceNumber(right.price)
            if leftPrice != rightPrice { return leftPrice < rightPrice }

            return left.name.localizedCaseInsensitiveCompare(right.name) == .orderedAscending
        }
    }
    var purchasableCount: Int {
        available.filter { $0.purchasableStatus == "purchasable" }.count
    }
    var history: [AvailabilityRecord] {
        var seen = Set<String>()
        return (payload?.history ?? [])
            .filter { $0.availableUntil != nil }
            .filter { item in
                let key = "\(item.productKey)|\(item.availableFrom)|\(item.availableUntil ?? "")"
                return seen.insert(key).inserted
            }
    }
    var lastCheckedAt: String { payload?.lastCheckedAt ?? "Not checked yet" }
    init() {
        let savedURL = UserDefaults.standard.string(forKey: "feedURL") ?? Self.defaultFeedURL
        if savedURL.contains("127.0.0.1") || savedURL.contains("localhost") {
            feedURL = Self.defaultFeedURL
            UserDefaults.standard.set(Self.defaultFeedURL, forKey: "feedURL")
        } else {
            feedURL = savedURL
        }
    }

    var groupedHistory: [ProductHistoryGroup] {
        let groups = Dictionary(grouping: history, by: { $0.productKey })
        return groups.values.map { records in
            let sorted = records.sorted { ($0.availableUntil ?? $0.availableFrom) > ($1.availableUntil ?? $1.availableFrom) }
            let latest = sorted[0]
            return ProductHistoryGroup(
                productKey: latest.productKey,
                name: latest.name,
                url: latest.url,
                price: latest.price,
                imageURL: latest.imageURL,
                records: sorted
            )
        }
        .sorted { left, right in
            let leftDate = left.latestRecord?.availableUntil ?? left.latestRecord?.availableFrom ?? ""
            let rightDate = right.latestRecord?.availableUntil ?? right.latestRecord?.availableFrom ?? ""
            return leftDate > rightDate
        }
    }

    func historyGroups(matchingSeries seriesName: String) -> [ProductHistoryGroup] {
        let targetKey = productSeriesKey(seriesName)
        return groupedHistory.filter { productSeriesKey($0.name) == targetKey }
    }

    func saveFeedURL() {
        UserDefaults.standard.set(feedURL, forKey: "feedURL")
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let loadedPayload = try await loadRemoteOrSample()
            payload = loadedPayload
            ProductImageCache.shared.preload(imageURLs(from: loadedPayload))
        } catch is CancellationError {
            return
        } catch let urlError as URLError where urlError.code == .cancelled {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadRemoteOrSample() async throws -> InventoryPayload {
        let trimmedURL = feedURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = URL(string: trimmedURL), !trimmedURL.isEmpty {
            var request = URLRequest(url: url)
            request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
            request.timeoutInterval = 20
            request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            request.setValue("no-cache", forHTTPHeaderField: "Pragma")

            let (data, response) = try await URLSession.shared.data(for: request)
            if let httpResponse = response as? HTTPURLResponse, !(200...299).contains(httpResponse.statusCode) {
                throw URLError(.badServerResponse)
            }
            return try JSONDecoder().decode(InventoryPayload.self, from: data)
        }
        if let sample = try loadSample() { return sample }
        throw URLError(.fileDoesNotExist)
    }

    private func loadSample() throws -> InventoryPayload? {
        guard let url = Bundle.main.url(forResource: "sample_inventory", withExtension: "json") else { return nil }
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(InventoryPayload.self, from: data)
    }

    private func imageURLs(from payload: InventoryPayload) -> [String] {
        var seen = Set<String>()
        let urls = payload.available.compactMap(\.imageURL) + payload.history.compactMap(\.imageURL)
        return urls.filter { seen.insert($0).inserted }
    }
}

@MainActor
final class SessionStore: ObservableObject {
    @Published var isLoggedIn = UserDefaults.standard.bool(forKey: "isLoggedIn")
    @Published var email = UserDefaults.standard.string(forKey: "accountEmail") ?? ""
    @Published var subscribedEmail = UserDefaults.standard.string(forKey: "subscribedEmail") ?? ""
    @Published var message: String?

    func signUp(email: String, password: String) {
        guard email.contains("@"), password.count >= 6 else {
            message = "Use a valid email and at least six password characters."
            return
        }
        UserDefaults.standard.set(email, forKey: "accountEmail")
        UserDefaults.standard.set(password, forKey: "accountPassword")
        UserDefaults.standard.set(true, forKey: "isLoggedIn")
        self.email = email
        isLoggedIn = true
        message = "Account created."
    }

    func login(email: String, password: String) {
        let savedEmail = UserDefaults.standard.string(forKey: "accountEmail")
        let savedPassword = UserDefaults.standard.string(forKey: "accountPassword")
        guard savedEmail == email, savedPassword == password else {
            message = "Email or password does not match this device."
            return
        }
        UserDefaults.standard.set(true, forKey: "isLoggedIn")
        self.email = email
        isLoggedIn = true
        message = "Signed in."
    }

    func subscribe(email: String) {
        guard isLoggedIn else {
            message = "Sign in before subscribing to alerts."
            return
        }
        guard email.contains("@") else {
            message = "Enter a valid alert email."
            return
        }
        UserDefaults.standard.set(email, forKey: "subscribedEmail")
        subscribedEmail = email
        message = "Subscribed for alerts."
    }

    func logout() {
        UserDefaults.standard.set(false, forKey: "isLoggedIn")
        isLoggedIn = false
        message = "Signed out."
    }
}

func purchasableRank(_ value: String?) -> Int {
    switch value ?? "unknown" {
    case "purchasable": return 0
    case "not_purchasable": return 1
    default: return 2
    }
}

func productSeriesName(_ name: String) -> String {
    let cleanName = cleanProductName(name)
    let normalized = normalizedSeriesText(cleanName)
    let knownSeries: [(String, String)] = [
        ("hermes cabasellier", "Hermès Cabasellier"),
        ("le petit sac", "Le Petit Sac"),
        ("picotin lock", "Picotin Lock"),
        ("neo garden", "Neo Garden"),
        ("garden party", "Garden Party"),
        ("so medor", "So Medor"),
        ("hermes videpoches", "Hermès Videpoches"),
        ("24/24", "24/24"),
        ("24 24", "24/24"),
        ("evelyne", "Evelyne"),
        ("kelly", "Kelly"),
        ("constance", "Constance"),
        ("herbag", "Herbag"),
        ("lindy", "Lindy"),
        ("bolide", "Bolide"),
        ("roulis", "Roulis"),
        ("jypsiere", "Jypsiere"),
        ("plume", "Plume"),
        ("halzan", "Halzan"),
        ("arcon", "Arcon"),
        ("balusoie", "Balusoie"),
        ("lassoie", "Lassoie")
    ]
    if let match = knownSeries.first(where: { normalized.hasPrefix($0.0) }) {
        return match.1
    }
    return fallbackSeriesName(cleanName)
}

func productSeriesKey(_ name: String) -> String {
    normalizedSeriesText(productSeriesName(name))
}

private func cleanProductName(_ name: String) -> String {
    name.replacingOccurrences(of: #"(?i)\s+bag$"#, with: "", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

private func normalizedSeriesText(_ name: String) -> String {
    name.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
        .replacingOccurrences(of: #"[^a-z0-9/ ]+"#, with: " ", options: .regularExpression)
        .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .lowercased()
}

private func fallbackSeriesName(_ name: String) -> String {
    let beforeDash = name.components(separatedBy: " - ").first ?? name
    let words = beforeDash.split(separator: " ").map(String.init)
    let stopWords: Set<String> = ["I", "II", "III", "IV", "V", "Mini", "Micro", "Poche", "Pocket"]
    var kept: [String] = []
    for word in words {
        if word.range(of: #"^\d+$"#, options: .regularExpression) != nil { break }
        if stopWords.contains(word) { break }
        kept.append(word)
    }
    if kept.first?.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current).lowercased() == "hermes", kept.count > 1 {
        kept.removeFirst()
    }
    return kept.isEmpty ? name : kept.joined(separator: " ")
}

func priceNumber(_ value: String?) -> Double {
    guard let value else { return .infinity }
    let cleaned = value.filter { $0.isNumber || $0 == "." }
    return Double(cleaned) ?? .infinity
}
