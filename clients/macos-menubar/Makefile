# Agent LB — macOS 26 menu bar app (SwiftPM executable + manual bundle assembly)
#
# Notarization caveat: the bundle uses an ad-hoc signature (codesign --sign -)
# which is valid only on the local machine.  Gatekeeper on other machines will
# reject it.  If notarized distribution is ever required, migrate to an Xcode
# project (xcodegen or .xcodeproj) with a Developer ID certificate and
# `codesign --sign "Developer ID Application: …"` + `xcrun notarytool submit`.
#
# CFBundleExecutable must match the binary name exactly (case-sensitive).
# Adding any entitlement later will require a signed identity, not ad-hoc.
# Launch-at-login (SMAppService.mainApp) only works from AgentLB.app, not
# from the bare `swift run` binary — use `make run` to test that path.
#
# IMPORTANT (macOS 26): launching the app with `open` from an SSH session
# runs the process but the menu bar item never registers.  `make install`
# runs it as a LaunchAgent in the GUI launchd domain instead, which both
# fixes that and makes the app start automatically at login.

APP      := AgentLB.app
BIN      := .build/release/AgentLB
BUNDLE_ID:= com.aneyman.agentlb.menubar
# The LaunchAgent runs the copy in ~/Applications (internal disk): launchd
# cannot reliably exec from /Volumes/StudioExt at login (TCC), and Raycast /
# Spotlight only index ~/Applications and /Applications.  Override with
#   make install-agent APP_PATH=/some/other/AgentLB.app
APP_PATH := $(HOME)/Applications/AgentLB.app
PLIST    := $(HOME)/Library/LaunchAgents/$(BUNDLE_ID).plist

.PHONY: build bundle run test clean install deploy install-agent uninstall

build:
	swift build -c release

bundle: build
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS $(APP)/Contents/Resources
	cp $(BIN) $(APP)/Contents/MacOS/AgentLB
	cp Resources/Info.plist $(APP)/Contents/Info.plist
	codesign --force --deep --sign - $(APP)

run: bundle
	open $(APP)

test:
	swift test

# Build, copy the bundle to ~/Applications, then register + start the app as
# a login LaunchAgent (GUI domain).
install: bundle deploy install-agent

# Copy the freshly assembled bundle to $(APP_PATH).
deploy:
	mkdir -p $(dir $(APP_PATH))
	rm -rf $(APP_PATH)
	ditto $(APP) $(APP_PATH)
	/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f $(APP_PATH)

# Write the LaunchAgent plist for $(APP_PATH) and (re)start the app.
# The /bin/sh wrapper waits for ControlCenter (the status-item host) before
# exec'ing the app: launched too early at login, the status item never
# registers and the process sits idle with no menu bar icon.  KeepAlive on
# non-zero exit pairs with StatusItemWatch, which exits 3 when the item is
# still missing after launch so launchd relaunches into a ready session.
install-agent:
	mkdir -p $(HOME)/Library/LaunchAgents
	printf '%s\n' \
	  '<?xml version="1.0" encoding="UTF-8"?>' \
	  '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	  '<plist version="1.0"><dict>' \
	  '  <key>Label</key><string>$(BUNDLE_ID)</string>' \
	  '  <key>ProgramArguments</key><array>' \
	  '    <string>/bin/sh</string>' \
	  '    <string>-c</string>' \
	  '    <string>i=0; until pgrep -xq ControlCenter || [ $$i -ge 90 ]; do sleep 1; i=$$((i+1)); done; sleep 3; exec "$$0"</string>' \
	  '    <string>$(APP_PATH)/Contents/MacOS/AgentLB</string>' \
	  '  </array>' \
	  '  <key>RunAtLoad</key><true/>' \
	  '  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>' \
	  '  <key>ThrottleInterval</key><integer>10</integer>' \
	  '  <key>ProcessType</key><string>Interactive</string>' \
	  '</dict></plist>' > $(PLIST)
	-launchctl bootout gui/$$(id -u)/$(BUNDLE_ID) 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(PLIST)

uninstall:
	-launchctl bootout gui/$$(id -u)/$(BUNDLE_ID) 2>/dev/null
	rm -f $(PLIST)

clean:
	swift package clean
	rm -rf $(APP)
