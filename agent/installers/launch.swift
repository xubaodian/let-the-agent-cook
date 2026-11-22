import Foundation

let terminal = Process()
terminal.executableURL = URL(fileURLWithPath: "/usr/bin/open")
terminal.arguments = ["-a", "Terminal", Bundle.main.url(forResource: "Agent Cook", withExtension: "command")!.path]
try terminal.run()
terminal.waitUntilExit()
exit(terminal.terminationStatus)
