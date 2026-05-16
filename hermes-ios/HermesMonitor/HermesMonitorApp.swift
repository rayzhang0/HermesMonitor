import SwiftUI

@main
struct HermesMonitorApp: App {
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
