.PHONY: validate validate-trust-chain-boot-verification test

validate: validate-trust-chain-boot-verification
	python src/sourceos_boot/validate_boot_release_set.py examples/boot-release-set.example.json
	python src/sourceos_boot/validate_boot_release_set.py \
	  examples/m2-recovery-installer/normal-boot.example.json \
	  examples/m2-recovery-installer/recovery-installer.example.json
	python -m json.tool examples/apple-silicon-evidence/raw-apple-silicon-evidence.example.json > /dev/null
	python -m json.tool examples/apple-silicon-evidence/normalized-boot-evidence.example.json > /dev/null
	python -m json.tool examples/artifact-build-lane/recovery-installer-build-result.example.json > /dev/null
	PYTHONPATH=src python -m sourceos_boot.cli adapt-nlboot \
	  --manifest examples/nlboot/manifest.json \
	  --token examples/nlboot/token.json \
	  --device-id device-demo-m2 \
	  --public-key-fingerprint sha256:0000000000000000000000000000000000000000000000000000000000000000 \
	  --platform apple-silicon \
	  --nonce nonce-demo-1 \
	  --correlation-id corr-demo-1

validate-trust-chain-boot-verification:
	python -m json.tool schemas/trust-chain-boot-verification-evidence.v0.1.schema.json > /dev/null
	python -m json.tool examples/trust-chain-boot-verification.valid.json > /dev/null
	python -m json.tool examples/trust-chain-boot-verification.blocked.json > /dev/null
	PYTHONPATH=src python -m sourceos_boot.validate_trust_chain_boot_verification

test:
	python -m pytest
