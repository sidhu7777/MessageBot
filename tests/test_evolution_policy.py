from src.evolution.policy import EvolutionAutoResponsePolicy


def test_evolution_policy_counts_and_resets_in_memory() -> None:
    policy = EvolutionAutoResponsePolicy(redis_client=None, session_window_seconds=60, key_prefix="test")

    assert policy.register_inbound(doctor_id=7, patient_identity="919999000111@s.whatsapp.net") == 1
    assert policy.register_inbound(doctor_id=7, patient_identity="919999000111@s.whatsapp.net") == 2
    assert policy.register_inbound(doctor_id=7, patient_identity="919999000111@s.whatsapp.net") == 3

    # Different doctor keeps a separate counter for the same patient.
    assert policy.register_inbound(doctor_id=8, patient_identity="919999000111@s.whatsapp.net") == 1
