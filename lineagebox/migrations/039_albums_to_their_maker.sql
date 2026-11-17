-- Albums and tours with no account on them are the admins' own, nobody's in particular: made before anyone signed
-- in, or by an admin, whose work used to be recorded that way (web/common._by). A couple now see what each other
-- adds (access.partner_shares), which needs to know who made a thing, so from here on an admin's album or tour is
-- recorded as theirs.
--
-- Where a library has exactly one admin, "the admins' own" and "that admin's own" are the same thing, so the ones
-- already in the catalog are given to them and reach their partner like the rest. With several admins there is no
-- telling which of them made what, so those are left as they are and are shared by hand as before. Nothing else
-- follows from an album's or tour's account: an admin may change any of them either way.
UPDATE album SET account_id = (SELECT id FROM account WHERE role = 'admin')
 WHERE account_id IS NULL AND (SELECT count(*) FROM account WHERE role = 'admin') = 1;
UPDATE tour SET account_id = (SELECT id FROM account WHERE role = 'admin')
 WHERE account_id IS NULL AND (SELECT count(*) FROM account WHERE role = 'admin') = 1;
