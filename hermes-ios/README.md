# Hermes Monitor iOS

SwiftUI app for viewing Hermes availability events exported by the monitor.

Open `HermesMonitor.xcodeproj` in Xcode, select an iPhone simulator, and run. The app ships with the public production inventory feed configured in `HermesMonitor/Info.plist`, and falls back to bundled sample data only if that feed is removed or unavailable.

The account/subscription UI is local-device scaffolding for now; backend subscriber persistence can be wired to the monitor API later.
