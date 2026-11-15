-- Artist gender (MusicBrainz: male/female/non-binary/other; NULL for groups).
ALTER TABLE artists ADD COLUMN gender TEXT;

-- MusicBrainz returns each genre in both `genres` and `tags`. Earlier
-- collections stored it twice (once as genre, once as descriptive). Drop the
-- descriptive copy wherever the same artist has a genre tag with that name.
DELETE FROM artist_tags
WHERE rowid IN (
    SELECT d.rowid
    FROM artist_tags d
    JOIN tags dt ON dt.id = d.tag_id AND dt.tag_type = 'descriptive'
    WHERE EXISTS (
        SELECT 1
        FROM artist_tags g
        JOIN tags gt ON gt.id = g.tag_id AND gt.tag_type = 'genre'
        WHERE g.artist_id = d.artist_id
          AND g.source = d.source
          AND gt.normalized_name = dt.normalized_name
    )
);

-- Genre tags filtered for recommender use. MusicBrainz genre tags are user
-- votes; 1-2 votes are often noise (e.g. a rapper tagged "house"). Keep a
-- genre when it has >= 3 votes, is the artist's top-voted genre, or was set
-- explicitly in seed config. `weight` is votes relative to the artist's top
-- genre, in (0, 1].
DROP VIEW IF EXISTS artist_genres;
CREATE VIEW artist_genres AS
WITH g AS (
    SELECT at.artist_id, t.normalized_name AS genre, at.source,
           COALESCE(at.count, 0) AS votes
    FROM artist_tags at
    JOIN tags t ON t.id = at.tag_id
    WHERE t.tag_type = 'genre'
),
mx AS (
    SELECT artist_id, MAX(votes) AS top_votes
    FROM g WHERE source = 'musicbrainz' GROUP BY artist_id
)
SELECT g.artist_id,
       g.genre,
       MAX(g.votes) AS votes,
       CASE
           WHEN MAX(g.source = 'seed') = 1 AND COALESCE(mx.top_votes, 0) = 0 THEN 1.0
           WHEN COALESCE(mx.top_votes, 0) = 0 THEN 1.0
           ELSE MIN(1.0, MAX(1.0 * g.votes / mx.top_votes, CASE WHEN MAX(g.source = 'seed') = 1 THEN 0.5 ELSE 0 END))
       END AS weight
FROM g
LEFT JOIN mx ON mx.artist_id = g.artist_id
GROUP BY g.artist_id, g.genre
HAVING MAX(g.votes) >= 3
    OR MAX(g.source = 'seed') = 1
    OR (mx.top_votes IS NOT NULL AND MAX(g.votes) = mx.top_votes);
