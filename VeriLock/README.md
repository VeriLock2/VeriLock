# VeriLock Program README

**Raspberry Pi Version One**

VeriLock is a Raspberry Pi 5 application that uses local facial recognition and RFID to operate a geared deadbolt. Normal mode accepts either enrolled credential. Ultra Secure mode requires both credentials from the same enrolled user. The original key provides manual operation.

## 1. Deployment

1. Use a Raspberry Pi 5 with 64-bit Raspberry Pi OS Trixie and Python 3.13. Install the OS-managed camera, GPIO, and Tk packages specified in Section 7 of the Program Specification.
2. Keep `requirements.txt` and `requirements-pi.lock` beside `rebuild_venv.sh`. With the VeriLock service stopped, run `bash ./rebuild_venv.sh` from the program directory.
3. Place the InsightFace `buffalo_s` model files under `~/.insightface/models/buffalo_s` for the account that runs the service.
4. Configure and install `verilock.service` with the correct service account, working directory, launcher path, and display session settings. With both UPS outputs available, start the installed service. VeriLock commands the bolt to lock during startup before accepting credentials. Observe the bolt to confirm its physical position.

```bash
sudo systemctl start verilock.service
sudo systemctl status verilock.service
sudo journalctl -u verilock.service -b
```

If startup fails, correct the cause shown in the service log before running `sudo systemctl restart verilock.service`. If the actuator UPS loses output while the Pi stays on, wait until both UPS outputs are available before restarting the service. See Section 12 of the Program Specification for startup, recovery, and backup procedures.

## 2. Bill of Materials

- [Hardware Bill of Materials (CSV)](https://drive.google.com/file/d/14gWtqxFQQb7GqCIK59zQxpPJEU718H08/view?usp=drive_link) lists the university-supplied hardware.
- [Hardware Bill of Materials (PDF)](https://drive.google.com/file/d/1GSNwE8U_Buw4plIVsa_awpaUfyiT23a5/view?usp=sharing) is the formatted hardware inventory.
- [Signed Software Bill of Materials (CycloneDX JSON)](https://drive.google.com/file/d/1fmvekdRjgKVymmYRupwYgfE2fMzdXmhS/view) records the software inventory for Pi Version 1.
- `VeriLock-SBOM-Public-Key.pem` contains the public key for verifying the SBOM’s embedded signature.

## 3. Engineering records

- Engineering decision log: `docs/decisions.md` from the repository root.
- Grype dependency scan for Pi Version 1: `docs/security/VeriLock-Dependency-Scan.json` from the repository root.

- Dependency scan review: `docs/security/VeriLock-Dependency-Scan-Review.md` from the repository root.

## 4. Continuous integration

The VeriLock Dependency Inventory and Scan workflow generates a CycloneDX 1.6 inventory from `requirements-pi.lock` and scans it with Grype. It also scans the signed release SBOM using the Debian 13 baseline. The generated inventory and both scan reports are available as GitHub Actions artifacts. The workflow runs on demand and when qualifying pushes or pull requests change the dependency lockfile, release SBOM, or workflow definition.
