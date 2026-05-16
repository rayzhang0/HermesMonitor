# Hermes Monitor iOS

SwiftUI app for viewing Hermes availability events exported by the monitor.

Open `HermesMonitor.xcodeproj` in Xcode, select an iPhone simulator, and run. The app uses bundled sample data unless a feed URL is provided through the `HermesFeedURL` Info.plist key or saved locally on the device.

The account/subscription UI is local-device scaffolding for now; backend subscriber persistence can be wired to the monitor API later.
