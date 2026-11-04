-- The last 30-second step an admin's sign-in code was used for (accounts.check_code), so each code works once even
-- after the web app starts again: it was kept in memory, which a restart forgot.
ALTER TABLE account ADD COLUMN code_step INTEGER;
