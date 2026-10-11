import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// chttr in the bar: shows whether you're dictating, transcribing or recording a
// meeting. Left click opens chttr's menu, right click starts or stops a meeting
// recording. Before setup, a click runs bin/setup in a terminal.
BarWidget {
  id: root
  moduleName: "robjobbed.chttr"

  readonly property string pluginDir: {
    var url = Qt.resolvedUrl(".").toString()
    if (url.indexOf("file://") === 0) url = url.substring(7)
    if (url.length > 1 && url.charAt(url.length - 1) === "/") url = url.substring(0, url.length - 1)
    return decodeURIComponent(url)
  }
  readonly property string exe: Quickshell.env("HOME") + "/.local/bin/chttr"
  property string state: "idle"

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: feed
    command: [root.pluginDir + "/bin/chttr-status"]
    running: true
    stdout: SplitParser {
      onRead: function (line) {
        try {
          var data = JSON.parse(line)
          root.state = String(data.alt || data["class"] || "idle")
        } catch (e) {}
      }
    }
    onExited: restart.start()
  }

  Timer {
    id: restart
    interval: 3000
    onTriggered: feed.running = true
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    fontFamily: "JetBrainsMono Nerd Font"
    horizontalMargin: 8
    text: {
      if (root.state === "recording") return "\u{F036C}"       // microphone
      if (root.state === "transcribing") return "\u{F0513}"    // timer sand
      if (root.state === "meeting") return "\u{F044A}"         // record
      if (root.state === "setup") return "\u{F036D}"           // microphone off
      return "\u{F036C}"
    }
    active: root.state === "recording" || root.state === "transcribing" || root.state === "meeting"
    dimmed: root.state === "setup" || root.state === "idle"
    tooltipText: {
      if (root.state === "setup") return "chttr: click to set up"
      if (root.state === "recording") return "chttr: listening"
      if (root.state === "transcribing") return "chttr: transcribing"
      if (root.state === "meeting") return "chttr: recording a meeting (right click to stop)"
      return "chttr: hold Insert to dictate · right click to record a meeting"
    }
    onPressed: function (code) {
      if (root.state === "setup") {
        Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", root.pluginDir + "/bin/setup"])
      } else if (code === Qt.RightButton) {
        Quickshell.execDetached([root.exe, "meeting", "toggle"])
      } else {
        Quickshell.execDetached([root.exe, "menu"])
      }
    }
  }
}
