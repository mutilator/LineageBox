-- How far ahead of the next likeliest person a face recognised automatically is: with assign_score, how sure its name
-- is (people.how_sure), which People → Name faces and Check faces show, least sure first, and which says yes to the
-- surest at once. Kept by recognition, and worked out again when the faces named by hand change
-- (people.measure_recognised).
ALTER TABLE face ADD COLUMN assign_lead REAL;
