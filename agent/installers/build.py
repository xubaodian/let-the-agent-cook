"""Freeze the locked CLI and build an installer for the current host."""

import argparse
import base64
import hashlib
import json
import os
import platform
import plistlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/agent_cook"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
WIX = "http://wixtoolset.org/schemas/v4/wxs"
UPGRADE_CODE = UUID("5b2de3a8-b19c-4b62-ae5e-1ef6df4479ef")


def call(*command: str | Path) -> None:
    result = subprocess.run(command)
    if result.returncode:
        raise SystemExit(f"{Path(command[0]).name} failed with exit status {result.returncode}.")


def setting(name: str) -> str:
    if not (value := os.environ.get(name)):
        raise SystemExit(f"Provide {name} to sign this installer.")
    return value


def secret_file(directory: Path, name: str, variable: str) -> Path:
    path = directory / name
    path.write_bytes(base64.b64decode(setting(variable), validate=True))
    return path


def freeze(work: Path, image: str, models: Path | None, identity: str | None) -> Path:
    data = shutil.copytree(SOURCE / "data", work / "data")
    (data / "release.json").write_text(json.dumps({"sandbox_image": image}), encoding="utf-8")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("Run the installer build with uv on PATH.")
    native = data / "native"
    shutil.copy2(uv, native / ("uv.exe" if os.name == "nt" else "uv"))
    shutil.copyfile(ROOT / ".python-version", native / ".python-version")
    if models is not None:
        from agent_cook.model.catalog import ModelCatalog

        ModelCatalog.load(models)
        shutil.copyfile(models, data / "models.json")
    signing = ("--codesign-identity", identity) if identity else ()
    call(
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "agent-cook",
        "--distpath", work / "dist", "--workpath", work / "work", "--specpath", work,
        "--collect-data", "agent_cook.context", "--add-data", f"{data}:agent_cook/data",
        *signing, SOURCE / "__main__.py",
    )
    return work / "dist/agent-cook"


def element(parent, tag, **attributes):
    return ET.SubElement(parent, f"{{{WIX}}}{tag}", attributes)


def write_wix(payload: Path, path: Path) -> None:
    # A registry key path per component lets Windows Installer validate a per-user install.
    ET.register_namespace("", WIX)
    doc = ET.Element(f"{{{WIX}}}Wix")
    package = element(
        doc, "Package", Name="Agent Cook", Manufacturer="Let the Agent Cook", Version=VERSION,
        UpgradeCode=str(UPGRADE_CODE), Scope="perUser", InstallerVersion="500",
        Codepage="65001",
    )
    element(package, "MajorUpgrade", DowngradeErrorMessage="A newer version of Agent Cook is already installed.")
    element(package, "MediaTemplate", EmbedCab="yes")
    local = element(package, "StandardDirectory", Id="LocalAppDataFolder")
    programs = element(local, "Directory", Id="LocalPrograms", Name="Programs")
    install = element(programs, "Directory", Id="InstallFolder", Name="Agent Cook")
    menu = element(package, "StandardDirectory", Id="ProgramMenuFolder")
    menu_dir = element(menu, "Directory", Id="AgentCookMenu", Name="Agent Cook")
    personal = element(package, "StandardDirectory", Id="PersonalFolder")
    feature = element(package, "Feature", Id="Core", Title="Agent Cook", Level="1")

    def component(parent, identifier):
        item = element(parent, "Component", Id=identifier, Guid=str(uuid5(UPGRADE_CODE, identifier)))
        element(
            item, "RegistryValue", Root="HKCU", Key=r"Software\Agent Cook\Installer",
            Name=identifier, Type="integer", Value="1", KeyPath="yes",
        )
        element(feature, "ComponentRef", Id=identifier)
        return item

    launcher = component(menu_dir, "StartMenuEntry")
    element(
        launcher, "Shortcut", Id="LaunchAgentCook", Name="Agent Cook", Target="[InstallFolder]agent-cook.exe",
        WorkingDirectory=personal.get("Id"), Arguments="--app",
    )
    element(launcher, "RemoveFolder", Id="AgentCookMenu", On="uninstall")

    dirs = {Path("."): install}
    for file in sorted(payload.rglob("*")):
        relative = file.relative_to(payload)
        token = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:32]
        parent = dirs[relative.parent]
        if file.is_dir():
            dirs[relative] = element(parent, "Directory", Id=f"D_{token}", Name=relative.name)
        else:
            item = component(parent, f"C_{token}")
            element(item, "File", Id=f"F_{token}", Name=relative.name, Source=str(file), KeyPath="no")
    ET.indent(doc)
    ET.ElementTree(doc).write(path, encoding="utf-8", xml_declaration=True)


def windows(payload: Path, output: Path, work: Path, signed: bool) -> None:
    installer = output / "AgentCook-windows-x64.msi"
    source = work / "AgentCook.wxs"
    if signed:
        cert = secret_file(work, "certificate.pfx", "WINDOWS_PFX_BASE64")
        # Never echo the command because signtool receives the certificate password.
        sdk = Path(os.environ["ProgramFiles(x86)"]) / "Windows Kits/10/bin"
        candidates = sorted(sdk.glob("*/x64/signtool.exe"), reverse=True)
        if not candidates:
            raise SystemExit("Install the Windows SDK to use signtool.")
        sign = (
            candidates[0], "sign", "/f", cert, "/p", setting("WINDOWS_PFX_PASSWORD"),
            "/fd", "SHA256", "/tr", "http://timestamp.digicert.com", "/td", "SHA256",
        )
        call(*sign, payload / "agent-cook.exe")
    write_wix(payload, source)
    call("wix", "build", "-arch", "x64", "-o", installer, source)
    if signed:
        call(*sign, installer)
        call(candidates[0], "verify", "/pa", installer)


@contextmanager
def macos_keychain(work: Path):
    path = work / "agent-cook.keychain-db"
    password = secrets.token_urlsafe(32)
    original = subprocess.check_output(
        ["security", "list-keychains", "-d", "user"], text=True,
    ).splitlines()
    search = [line.strip().strip('"') for line in original if line.strip()]
    call("security", "create-keychain", "-p", password, path)
    try:
        call("security", "set-keychain-settings", "-lut", "3600", path)
        call("security", "unlock-keychain", "-p", password, path)
        call("security", "list-keychains", "-d", "user", "-s", path, *search)
        for kind in ("APP", "INSTALLER"):
            cert = secret_file(work, f"{kind}.p12", f"MACOS_{kind}_P12_BASE64")
            call(
                "security", "import", cert, "-k", path, "-P", setting(f"MACOS_{kind}_P12_PASSWORD"),
                "-T", "/usr/bin/codesign", "-T", "/usr/bin/productbuild", "-T", "/usr/bin/productsign",
            )
        call("security", "set-key-partition-list", "-S", "apple-tool:,apple:", "-s", "-k", password, path)
        yield
    finally:
        subprocess.run(["security", "list-keychains", "-d", "user", "-s", *search], check=False)
        subprocess.run(["security", "delete-keychain", path], check=False)


def macos(payload: Path, output: Path, work: Path, signed: bool) -> None:
    app = work / "Agent Cook.app"
    content = app / "Contents"
    binary = content / "MacOS/Agent Cook"
    binary.parent.mkdir(parents=True)
    resources = content / "Resources"
    resources.mkdir()
    payload.rename(resources / payload.name)
    shell = resources / "Agent Cook.command"
    shell.write_text('#!/bin/sh\nexec "$(dirname "$0")/agent-cook/agent-cook" --app\n', encoding="utf-8")
    shell.chmod(0o755)
    call("swiftc", ROOT / "installers/launch.swift", "-o", binary)
    (content / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleName": "Agent Cook", "CFBundleDisplayName": "Agent Cook",
        "CFBundleExecutable": "Agent Cook", "CFBundleIdentifier": "com.lettheagentcook.cli",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": VERSION, "CFBundleVersion": VERSION,
    }))
    installer = output / "AgentCook-macos-arm64.pkg"
    signing = ()
    if signed:
        call("codesign", "--force", "--options", "runtime", "--timestamp", "--sign", setting("MACOS_APP_IDENTITY"), app)
        call("codesign", "--verify", "--deep", "--strict", app)
        signing = ("--sign", setting("MACOS_INSTALLER_IDENTITY"))
    call("productbuild", "--component", app, "/Applications", *signing, installer)
    if signed:
        key = secret_file(work, "NotaryKey.p8", "MACOS_NOTARY_KEY_BASE64")
        call(
            "xcrun", "notarytool", "submit", installer, "--key", key, "--key-id", setting("MACOS_NOTARY_KEY_ID"),
            "--issuer", setting("MACOS_NOTARY_ISSUER"), "--wait",
        )
        call("xcrun", "stapler", "staple", installer)
        call("xcrun", "stapler", "validate", installer)


def linux(payload: Path, output: Path, work: Path) -> None:
    base = work / "deb"
    for part in ("DEBIAN", "opt", "usr/bin", "usr/share/applications"):
        (base / part).mkdir(parents=True)
    payload.rename(base / "opt/agent-cook")
    command = base / "usr/bin/agent-cook"
    command.write_text('#!/bin/sh\nexec /opt/agent-cook/agent-cook "$@"\n', encoding="utf-8")
    command.chmod(0o755)
    (base / "usr/share/applications/agent-cook.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Agent Cook\nExec=/usr/bin/agent-cook --app\n"
        "Terminal=true\nCategories=Utility;\n", encoding="utf-8",
    )
    (base / "DEBIAN/control").write_text(
        f"Package: agent-cook\nVersion: {VERSION}\nArchitecture: amd64\n"
        "Maintainer: Let the Agent Cook <22562011+xubaodian@users.noreply.github.com>\n"
        "Depends: libc6 (>= 2.39), bubblewrap, apparmor-profiles, "
        "libreoffice-impress | libreoffice-impress-nogui, fonts-noto-cjk\n"
        "Recommends: gnome-keyring, xdg-utils\n"
        "Description: Agent Cook command line application\n", encoding="utf-8",
    )
    postinst = base / "DEBIAN/postinst"
    postinst.write_text(
        '#!/bin/sh\nset -eu\n'
        'extra=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict\n'
        'profile=/etc/apparmor.d/bwrap-userns-restrict\n'
        'if [ "$1" = configure ] && [ -r "$extra" ] && [ -r /sys/kernel/security/apparmor/profiles ] '
        '&& ! grep -q "^bwrap (" /sys/kernel/security/apparmor/profiles '
        '&& [ ! -e "$profile" ] && [ ! -L "$profile" ]; then\n'
        '    if apparmor_parser -a "$extra"; then install -m 0644 "$extra" "$profile"; fi\n'
        'fi\n', encoding="utf-8",
    )
    postinst.chmod(0o755)
    call("dpkg-deb", "--root-owner-group", "--build", base, output / "AgentCook-ubuntu-amd64.deb")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--models", type=Path)
    parser.add_argument("--sign", action="store_true")
    args = parser.parse_args()
    os_name, arch = platform.system(), platform.machine().lower()
    if (os_name, arch) not in {("Linux", "x86_64"), ("Darwin", "arm64"), ("Windows", "amd64")}:
        parser.error("Build on Ubuntu x64, macOS Apple Silicon, or Windows x64.")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-cook-build-") as directory:
        work = Path(directory)
        if os_name == "Darwin":
            if args.sign:
                with macos_keychain(work):
                    payload = freeze(work, args.image, args.models, setting("MACOS_APP_IDENTITY"))
                    macos(payload, output, work, signed=True)
            else:
                macos(freeze(work, args.image, args.models, None), output, work, signed=False)
        else:
            payload = freeze(work, args.image, args.models, None)
            if os_name == "Windows":
                windows(payload, output, work, args.sign)
            else:
                linux(payload, output, work)


if __name__ == "__main__":
    main()
