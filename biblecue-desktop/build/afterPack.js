/**
 * electron-builder afterPack hook.
 *
 * BibleCue is not distributed with an Apple Developer ID, so electron-builder
 * skips macOS signing entirely. An unsigned app is bad enough (Gatekeeper
 * warning), but the real problem is the bundled Python backend at
 * Contents/Resources/python/backend: macOS silently kills an unsigned,
 * quarantined helper process, so "Start Listening" does nothing.
 *
 * Applying an ad-hoc signature (`codesign --sign -`) to the backend and then
 * to the whole bundle lets the helper run once the user approves the app
 * (right-click -> Open, or `xattr -dr com.apple.quarantine`). It does NOT
 * remove the "unverified developer" prompt — only notarization does that.
 */
const { execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return;

  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);
  const backend = path.join(appPath, 'Contents', 'Resources', 'python', 'backend');

  const sign = (target) => {
    execFileSync('codesign', ['--force', '--sign', '-', '--timestamp=none', target], {
      stdio: 'inherit',
    });
  };

  // Inner code must be signed before the enclosing bundle is sealed.
  if (fs.existsSync(backend)) {
    console.log(`  • ad-hoc signing bundled backend  ${backend}`);
    sign(backend);
  }
  console.log(`  • ad-hoc signing app bundle  ${appPath}`);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', '--timestamp=none', appPath], {
    stdio: 'inherit',
  });
};
