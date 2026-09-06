# Contributing

Use a focused branch and describe the affected target: vanilla Terraria, tModLoader, or both.

Before submitting a pull request:

1. Run `scripts\test.ps1`.
2. Run `scripts\build.ps1`.
3. Perform relevant live display and runtime validation.
4. Never include Terraria, tModLoader, Steam, save-game, or user configuration files.
5. Do not add telemetry, automatic downloads, or silent network access.

Bug reports should include the application version, target version, Windows version, monitor geometry, selected options, reproduction steps, and an exported diagnostic log. Remove personal paths if needed.
