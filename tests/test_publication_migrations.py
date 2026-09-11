import hashlib
import pytest
from factorlab.migrations import MIGRATIONS

FROZEN = {1: '37f44d87d677b6f261029a99dae83acb49cde3bcac4aca9a8ec5f8ce287a0878', 2: 'df017594f01cf5b5315abba1e01b8fd9a361742a724fdca5db461bf7f415da4d', 3: '0de41289728114933208ee8e4840b76a14ede7abf3c9ad23189878712b3550a9', 4: '363302271b9a2bb6ec56e44ad1a27c6db1823a869744e6fb1248ef8c73e4c144', 5: '86abb0a7d7181696339815a68251a390da14989cfecea602080d2c11a2e3d1e8', 6: '1c4617620473073c3bfaefaff7d1e962502150b5042f1bb8c3bae9285df0ba3a', 7: '6d8c059f37400d300356f5de5d44ef776d606b2a049531c5d13cdc9175f15700', 8: 'bdcffe1a6c490ba970c15cdc43d269418a126d7ded87521c8bbb7d191c4e5563', 9: 'e7bcc8b185c0e208dff731de92c8833d0124e3c254ed8e113fa2a3d9022a95b6', 10: '2fd14f2ecbfeeaf74a7f96f9f4ac3fb00ac9a13544609e709165fcdd5dec9408', 11: 'bcf1af52fdda6cb87d407fc0e4ecc0e0be456e215f0aeee1019132e8bb274955', 12: 'e67ab4d832efb30285d6984523865be687510bc0a88c858ca48d78f189d2c0e7', 13: '72019fabdfeb51d39449abe88f897edbf0ba6c1905eeabcb4cb33e3ca9ef2fc0'}

@pytest.mark.parametrize("version", sorted(FROZEN))
def test_old_migration_text_is_unchanged(version):
    assert hashlib.sha256(MIGRATIONS[version].encode()).hexdigest() == FROZEN[version]

def test_additive_publication_migration_inventory():
    assert sorted(MIGRATIONS) == list(range(1,15))
    assert "DROP TABLE" not in MIGRATIONS[14]
    assert "DELETE FROM universe_snapshots" not in MIGRATIONS[14]
    assert "SECURITY DEFINER" not in MIGRATIONS[14]
