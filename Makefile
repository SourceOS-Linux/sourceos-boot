.PHONY: validate test

validate:
	python src/sourceos_boot/validate_boot_release_set.py examples/boot-release-set.example.json
	python src/sourceos_boot/validate_boot_release_set.py \
	  examples/m2-recovery-installer/normal-boot.example.json \
	  examples/m2-recovery-installer/recovery-installer.example.json
	python -m json.tool examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json > /dev/null
	python -m json.tool examples/apple-silicon-evidence/normalized-boot-evidence.example.json > /dev/null
	PYTHONPATH=src python -m sourceos_boot.cli adapt-nlboot \
	  --manifest examples/nlboot/manifest.json \
	  --token examples/nlboot/token.json \
	  --device-id device-demo-m2 \
	  --public-key-fingerprint sha256:0000000000000000000000000000000000000000000000000000000000000000 \
	  --platform apple-silicon \
	  --nonce nonce-demo-1 \
	  --correlation-id corr-demo-1

test:
	python -m pytest
