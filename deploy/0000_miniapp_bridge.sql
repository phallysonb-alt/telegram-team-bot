CREATE TABLE bridge_state (key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL);
--> statement-breakpoint
CREATE TABLE bridge_jobs (id TEXT PRIMARY KEY NOT NULL,path TEXT NOT NULL,payload TEXT NOT NULL,uid INTEGER NOT NULL,created INTEGER NOT NULL,deadline INTEGER NOT NULL,state TEXT NOT NULL,lease INTEGER,status INTEGER,result TEXT);
--> statement-breakpoint
CREATE INDEX bridge_jobs_pending ON bridge_jobs(state,created);
--> statement-breakpoint
CREATE INDEX bridge_jobs_user ON bridge_jobs(uid,created);
