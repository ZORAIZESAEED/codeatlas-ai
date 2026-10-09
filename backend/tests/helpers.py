import io
import stat
import zipfile


def make_zip(files: dict[str, bytes | str], symlinks: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        for name, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, target)
    return buffer.getvalue()


SAMPLE_REPO = {
    "sample-main/README.md": "# Sample\n\nHello.\n",
    "sample-main/app/main.py": "def main():\n    return 42\n",
    "sample-main/app/util.py": "X = 1",
    "sample-main/web/index.ts": "export const a = 1;\nexport const b = 2;\n",
    "sample-main/web/App.tsx": "export default function App() { return null }\n",
    "sample-main/config.yaml": "key: value\n",
    "sample-main/node_modules/lib/index.js": "module.exports = 1;\n",
    "sample-main/.git/config": "[core]\n",
    "sample-main/.env": "SECRET=1\n",
    "sample-main/logo.png": b"\x89PNG\r\n\x1a\n\x00\x00",
    "sample-main/fake.py": b"binary\x00data",
}
