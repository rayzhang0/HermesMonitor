import SwiftUI
import UIKit
import UserNotifications

@main
struct HermesMonitorApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var inventory = InventoryStore()
    @StateObject private var session = SessionStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(inventory)
                .environmentObject(session)
                .task { await inventory.load() }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, @preconcurrency UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        requestNotificationPermission(application)
        return true
    }

    private func requestNotificationPermission(_ application: UIApplication) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound]) { granted, _ in
            guard granted else { return }
            DispatchQueue.main.async {
                application.registerForRemoteNotifications()
            }
        }
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task {
            await PushTokenRegistrar.register(token: token)
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("Remote notification registration failed: \(error.localizedDescription)")
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .list])
    }
}

enum PushTokenRegistrar {
    private static let defaultFeedURL = Bundle.main.object(forInfoDictionaryKey: "HermesFeedURL") as? String ?? ""

    static func register(token: String) async {
        guard let url = registrationURL() else { return }
        let cacheKey = "lastRegisteredPushToken"
        let urlKey = "lastRegisteredPushURL"
        if UserDefaults.standard.string(forKey: cacheKey) == token,
           UserDefaults.standard.string(forKey: urlKey) == url.absoluteString {
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        let payload = [
            "token": token,
            "platform": "ios",
            "app_version": [appVersion, build].filter { !$0.isEmpty }.joined(separator: " ")
        ]

        do {
            request.httpBody = try JSONSerialization.data(withJSONObject: payload)
            let (_, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, (200...299).contains(httpResponse.statusCode) else {
                return
            }
            UserDefaults.standard.set(token, forKey: cacheKey)
            UserDefaults.standard.set(url.absoluteString, forKey: urlKey)
        } catch {
            print("Push token registration failed: \(error.localizedDescription)")
        }
    }

    private static func registrationURL() -> URL? {
        let feed = UserDefaults.standard.string(forKey: "feedURL") ?? defaultFeedURL
        guard var components = URLComponents(string: feed), !feed.isEmpty else { return nil }
        var path = components.path
        if path.hasSuffix("/public_inventory.json") {
            path.removeLast("/public_inventory.json".count)
        }
        components.path = path + "/push/register"
        components.query = nil
        components.fragment = nil
        return components.url
    }
}
