import SwiftUI
import UIKit

struct ContentView: View {
    @EnvironmentObject private var inventory: InventoryStore

    var body: some View {
        TabView {
            AvailableView()
                .tabItem { Label("Available", systemImage: "bag") }
            HistoryView()
                .tabItem { Label("History", systemImage: "clock") }
        }
        .tint(.brandCopper)
    }
}

struct AvailableView: View {
    @EnvironmentObject private var inventory: InventoryStore

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HeaderBlock(title: "Hermes Monitor", subtitle: "Track the purchasable status of Hermès bags on the official website.")
                    StatusStrip(visibleCount: inventory.available.count, purchasableCount: inventory.purchasableCount, lastChecked: inventory.lastCheckedAt)
                    if let error = inventory.errorMessage {
                        ErrorBanner(message: error)
                    }
                    if inventory.available.isEmpty {
                        EmptyState(title: "No visible bags", text: "When products are visible on the monitored page, they will show here.")
                    } else {
                        LazyVStack(spacing: 14) {
                            ForEach(inventory.available) { product in
                                ProductCard(product: product)
                            }
                        }
                    }
                }
                .padding(20)
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle("Available")
            .refreshable { await inventory.load() }
            .toolbar {
                Button { Task { await inventory.load() } } label: {
                    if inventory.isLoading {
                        ProgressView()
                    } else {
                        Image(systemName: "arrow.clockwise")
                    }
                }
                .disabled(inventory.isLoading)
            }
        }
    }
}

struct HistoryView: View {
    @EnvironmentObject private var inventory: InventoryStore

    var body: some View {
        NavigationStack {
            List {
                ForEach(inventory.groupedHistory) { group in
                    Section {
                        HistoryProductCard(group: group, showsSimilarLink: true)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.background)
            .navigationTitle("History")
        }
    }
}

struct SimilarProductsView: View {
    @EnvironmentObject private var inventory: InventoryStore
    let seriesName: String

    private var groups: [ProductHistoryGroup] {
        inventory.historyGroups(matchingSeries: seriesName)
    }

    var body: some View {
        List {
            ForEach(groups) { group in
                Section {
                    HistoryProductCard(group: group, showsSimilarLink: false)
                }
            }
        }
        .scrollContentBackground(.hidden)
        .background(Theme.background)
        .navigationTitle(seriesName)
    }
}

struct HistoryProductCard: View {
    let group: ProductHistoryGroup
    let showsSimilarLink: Bool
    @State private var showsFullHistory = false
    @State private var showsSimilarProducts = false

    private var displayedRecords: [AvailabilityRecord] {
        showsFullHistory ? group.records : Array(group.records.prefix(3))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 12) {
                ProductThumbnail(imageURL: group.imageURL)
                    .frame(width: 76, height: 76)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .top) {
                        Text(group.name).font(.headline)
                        Spacer()
                        Text(group.price ?? "")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(Color.brandCopper)
                    }
                    Text(productCode(from: group.url))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
            }
            VStack(alignment: .leading, spacing: 10) {
                ForEach(displayedRecords, id: \.id) { item in
                    HistoryRecordBlock(item: item)
                }
                if group.records.count > 3 {
                    Button(showsFullHistory ? "Show less" : "Show more") {
                        withAnimation(.snappy) {
                            showsFullHistory.toggle()
                        }
                    }
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Color.brandCopper)
                    .buttonStyle(.plain)
                }
            }
            HStack(spacing: 8) {
                ProductOpenButton(urlString: group.url)
                if showsSimilarLink {
                    Button {
                        showsSimilarProducts = true
                    } label: {
                        HistoryActionLabel(title: "Similar Products", systemImage: "sparkle.magnifyingglass")
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.vertical, 8)
        .navigationDestination(isPresented: $showsSimilarProducts) {
            SimilarProductsView(seriesName: productSeriesName(group.name))
        }
    }
}

struct HistoryRecordBlock: View {
    let item: AvailabilityRecord

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("From \(friendlyDate(item.availableFrom))")
            Text(item.availableUntil.map { "Until \(friendlyDate($0))" } ?? "Still visible")
            Text(item.availableUntil.map { "Visible for \(durationText(from: item.availableFrom, to: $0))" } ?? "Visible for \(elapsedText(since: item.availableFrom))")
                .foregroundStyle(Color.brandCopper)
        }
        .font(.footnote)
        .padding(.vertical, 4)
    }
}

struct AccountView: View {
    @EnvironmentObject private var session: SessionStore
    @EnvironmentObject private var inventory: InventoryStore
    @State private var email = ""
    @State private var password = ""
    @State private var alertEmail = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HeaderBlock(title: "Alerts", subtitle: "Browsing is open to everyone. Email subscriptions unlock after sign in.")
                    VStack(alignment: .leading, spacing: 12) {
                        Text(session.isLoggedIn ? "Signed in as \(session.email)" : "Create account or sign in")
                            .font(.headline)
                        TextField("Email", text: $email).textContentType(.emailAddress).keyboardType(.emailAddress).textFieldStyle(.roundedBorder)
                        SecureField("Password", text: $password).textFieldStyle(.roundedBorder)
                        HStack {
                            Button("Sign Up") { session.signUp(email: email, password: password) }
                            Button("Log In") { session.login(email: email, password: password) }
                            if session.isLoggedIn { Button("Log Out") { session.logout() } }
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .panelStyle()

                    VStack(alignment: .leading, spacing: 12) {
                        Text("Subscribe").font(.headline)
                        TextField("Alert email", text: $alertEmail).textFieldStyle(.roundedBorder)
                        Button("Save Alert Email") { session.subscribe(email: alertEmail) }
                            .buttonStyle(.borderedProminent)
                        Text(session.subscribedEmail.isEmpty ? "No alert email saved on this device." : "Subscribed: \(session.subscribedEmail)")
                            .foregroundStyle(.secondary)
                    }
                    .panelStyle()

                    VStack(alignment: .leading, spacing: 12) {
                        Text("Data Feed").font(.headline)
                        TextField("https://example.com/public_inventory.json", text: $inventory.feedURL)
                            .textFieldStyle(.roundedBorder)
                            .keyboardType(.URL)
                            .autocapitalization(.none)
                        Button("Save Feed URL") {
                            inventory.saveFeedURL()
                            Task { await inventory.load() }
                        }
                        .buttonStyle(.bordered)
                    }
                    .panelStyle()

                    if let message = session.message { Text(message).foregroundStyle(.secondary) }
                    if let error = inventory.errorMessage { Text(error).foregroundStyle(.red) }
                }
                .padding(20)
            }
            .background(Theme.background.ignoresSafeArea())
            .navigationTitle("Account")
        }
    }
}


@MainActor
final class ProductImageCache {
    static let shared = ProductImageCache()

    private let cache = NSCache<NSURL, UIImage>()
    private var loading = Set<URL>()

    private init() {
        cache.countLimit = 120
    }

    func image(for url: URL) -> UIImage? {
        cache.object(forKey: url as NSURL)
    }

    func preload(_ urlStrings: [String]) {
        for urlString in urlStrings.prefix(60) {
            guard let url = URL(string: urlString), image(for: url) == nil, !loading.contains(url) else { continue }
            Task { _ = await load(url) }
        }
    }

    func load(_ url: URL) async -> UIImage? {
        if let cached = image(for: url) { return cached }
        if loading.contains(url) { return nil }
        loading.insert(url)
        defer { loading.remove(url) }

        do {
            let (data, response) = try await URLSession.shared.data(from: url)
            guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode), let image = UIImage(data: data) else {
                return nil
            }
            cache.setObject(image, forKey: url as NSURL)
            return image
        } catch {
            return nil
        }
    }
}

@MainActor
final class ProductImageLoader: ObservableObject {
    @Published private(set) var image: UIImage?
    @Published private(set) var isLoading = false

    func load(_ imageURL: String?) async {
        guard let imageURL, let url = URL(string: imageURL) else {
            image = nil
            return
        }
        if let cached = ProductImageCache.shared.image(for: url) {
            image = cached
            return
        }
        isLoading = true
        defer { isLoading = false }
        image = await ProductImageCache.shared.load(url)
    }
}

struct ImageSheetItem: Identifiable {
    let urlString: String
    var id: String { urlString }
}

struct ProductImageButton: View {
    let imageURL: String?
    let contentMode: ContentMode
    let height: CGFloat
    let placeholderSize: CGFloat
    var maxImageWidth: CGFloat? = nil

    @State private var selectedImage: ImageSheetItem?

    var body: some View {
        Button {
            guard let imageURL else { return }
            selectedImage = ImageSheetItem(urlString: imageURL)
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 8).fill(Theme.imageSurface)
                CachedProductImage(imageURL: imageURL, contentMode: contentMode, placeholderSize: placeholderSize, maxImageWidth: maxImageWidth)
            }
            .frame(height: height)
            .frame(maxWidth: .infinity)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .contentShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .disabled(imageURL == nil)
        .sheet(item: $selectedImage) { item in
            ProductImageViewer(imageURL: item.urlString)
        }
    }
}

struct CachedProductImage: View {
    let imageURL: String?
    let contentMode: ContentMode
    let placeholderSize: CGFloat
    var maxImageWidth: CGFloat? = nil

    @StateObject private var loader = ProductImageLoader()

    var body: some View {
        Group {
            if let image = loader.image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
                    .frame(maxWidth: maxImageWidth ?? .infinity)
            } else {
                LogoMark().frame(width: placeholderSize, height: placeholderSize)
                    .opacity(loader.isLoading ? 0.55 : 1)
            }
        }
        .task(id: imageURL) {
            await loader.load(imageURL)
        }
    }
}

struct ProductImageViewer: View {
    @Environment(\.dismiss) private var dismiss
    let imageURL: String

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.background.ignoresSafeArea()
                CachedProductImage(imageURL: imageURL, contentMode: .fit, placeholderSize: 96)
                    .padding(18)
            }
            .navigationTitle("Product image")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                Button("Done") { dismiss() }
            }
        }
    }
}

struct ProductThumbnail: View {
    let imageURL: String?

    var body: some View {
        ProductImageButton(imageURL: imageURL, contentMode: .fill, height: 76, placeholderSize: 32)
    }
}

struct ProductCard: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    let product: TrackedProduct

    private var usesPadLayout: Bool { horizontalSizeClass == .regular }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ProductImageButton(
                imageURL: product.imageURL,
                contentMode: usesPadLayout ? .fit : .fill,
                height: usesPadLayout ? 260 : 210,
                placeholderSize: 92,
                maxImageWidth: usesPadLayout ? 460 : nil
            )

            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(product.name).font(.title3.weight(.semibold))
                }
                Spacer()
                Text(product.price ?? "Price pending")
                    .font(.headline)
                    .foregroundStyle(Color.brandCopper)
            }
            if let firstSeen = product.firstSeenAt {
                Text("Visible since \(friendlyDate(firstSeen))")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Text("Visible for \(elapsedText(since: firstSeen))")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Color.brandCopper)
            }
            PurchasableLine(status: product.purchasableStatus, checkedAt: product.purchasableCheckedAt)
            ProductOpenButton(urlString: product.url)
        }
        .panelStyle()
    }
}


struct ProductOpenButton: View {
    @Environment(\.openURL) private var openURL
    let urlString: String

    var body: some View {
        Button {
            guard let url = URL(string: urlString) else { return }
            openURL(url)
        } label: {
            HistoryActionLabel(title: "Open Product", systemImage: "arrow.up.right")
        }
        .buttonStyle(.plain)
    }
}

struct HistoryActionLabel: View {
    let title: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: systemImage)
                .imageScale(.small)
            Text(title)
        }
            .font(.caption.weight(.semibold))
            .lineLimit(1)
            .foregroundStyle(Color.brandCopper)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(
                Capsule()
                    .fill(Color.brandCopper.opacity(0.11))
            )
            .overlay(
                Capsule()
                    .stroke(Color.brandCopper.opacity(0.18), lineWidth: 1)
            )
    }
}

struct PurchasableLine: View {
    let status: String?
    let checkedAt: String?

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { timeline in
            HStack(spacing: 8) {
                Circle()
                    .fill(statusColor)
                    .frame(width: 8, height: 8)
                Text("Purchasable: \(displayStatus)")
                    .font(.footnote.weight(.semibold))
                Spacer(minLength: 8)
                if let checkedAt {
                    Text("checked \(relativeTimeAgo(checkedAt, now: timeline.date))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Text("Not checked yet")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var displayStatus: String {
        switch status ?? "unknown" {
        case "purchasable": return "Yes"
        case "not_purchasable": return "No"
        default: return "Unknown"
        }
    }

    private var statusColor: Color {
        switch status ?? "unknown" {
        case "purchasable": return .green
        case "not_purchasable": return .red
        default: return .gray
        }
    }
}

struct HeaderBlock: View {
    let title: String
    let subtitle: String

    var body: some View {
        HStack(spacing: 14) {
            LogoMark().frame(width: 58, height: 58)
            VStack(alignment: .leading, spacing: 5) {
                Text(title).font(.largeTitle.weight(.bold))
                Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
            }
        }
    }
}

struct StatusStrip: View {
    let visibleCount: Int
    let purchasableCount: Int
    let lastChecked: String

    var body: some View {
        HStack {
            Label("\(purchasableCount) out of \(visibleCount) purchasable", systemImage: "bag.fill")
            Spacer()
            Text(friendlyDate(lastChecked)).foregroundStyle(.secondary)
        }
        .font(.subheadline.weight(.medium))
        .padding(14)
        .background(Theme.panel)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct ErrorBanner: View {
    let message: String

    var body: some View {
        Label(message, systemImage: "exclamationmark.triangle.fill")
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.red)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.red.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }
}

struct EmptyState: View {
    let title: String
    let text: String

    var body: some View {
        VStack(spacing: 10) {
            LogoMark().frame(width: 84, height: 84)
            Text(title).font(.title3.weight(.semibold))
            Text(text).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(28)
        .panelStyle()
    }
}

struct LogoMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8).fill(LinearGradient(colors: [.brandInk, .brandCopper], startPoint: .topLeading, endPoint: .bottomTrailing))
            Text("H")
                .font(.system(size: 36, weight: .black, design: .serif))
                .foregroundStyle(.white)
            RoundedRectangle(cornerRadius: 8).stroke(.white.opacity(0.35), lineWidth: 1)
        }
    }
}

struct Theme {
    static let background = Color(red: 0.98, green: 0.965, blue: 0.935)
    static let panel = Color.white.opacity(0.78)
    static let imageSurface = Color(red: 0.91, green: 0.875, blue: 0.82)
}

extension Color {
    static let brandCopper = Color(red: 0.67, green: 0.32, blue: 0.14)
    static let brandInk = Color(red: 0.09, green: 0.075, blue: 0.06)
}

extension View {
    func panelStyle() -> some View {
        self.padding(16)
            .background(Theme.panel)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .shadow(color: .black.opacity(0.08), radius: 14, x: 0, y: 8)
    }
}

func friendlyDate(_ value: String) -> String {
    guard let date = parseISODate(value) else { return value }
    let output = DateFormatter()
    output.timeZone = TimeZone(identifier: "America/Los_Angeles")
    output.dateFormat = "MMM d, h:mm a"
    return output.string(from: date) + " PT"
}

func elapsedText(since value: String) -> String {
    guard let start = parseISODate(value) else { return "unknown" }
    return durationText(from: start, to: Date())
}

func relativeTimeAgo(_ value: String, now: Date = Date()) -> String {
    guard let start = parseISODate(value) else { return value }
    let seconds = max(0, Int(now.timeIntervalSince(start)))
    if seconds < 60 { return "just now" }
    return durationText(from: start, to: now) + " ago"
}

func durationText(from startValue: String, to endValue: String) -> String {
    guard let start = parseISODate(startValue), let end = parseISODate(endValue) else { return "unknown" }
    return durationText(from: start, to: end)
}

func durationText(from start: Date, to end: Date) -> String {
    let seconds = max(0, Int(end.timeIntervalSince(start)))
    let days = seconds / 86_400
    let hours = (seconds % 86_400) / 3_600
    let minutes = (seconds % 3_600) / 60
    if days > 0 { return "\(days)d \(hours)h" }
    if hours > 0 { return "\(hours)h \(minutes)m" }
    return "\(minutes)m"
}

func parseISODate(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = formatter.date(from: value) { return date }
    formatter.formatOptions = [.withInternetDateTime]
    return formatter.date(from: value)
}

func productCode(from urlString: String) -> String {
    guard let lastPathComponent = URL(string: urlString)?.lastPathComponent else { return "Product code unavailable" }
    let parts = lastPathComponent.split(separator: "-")
    guard let code = parts.last, code.first == "H" else { return "Product code unavailable" }
    return String(code)
}
