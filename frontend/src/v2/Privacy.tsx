import { Link } from 'react-router-dom';

export default function Privacy() {
  return (
    <article className="surface form-stack privacy-page">
      <h1>Data and privacy</h1>
      <section>
        <h2>Your workspace</h2>
        <p>
          The server stores your account, private transcripts, keyword reports, saved metadata and
          activity in its database. For a self-hosted installation, the person operating that server
          controls the database and its backups.
        </p>
      </section>
      <section>
        <h2>Speech and draft generation</h2>
        <p>
          Media analysis sends audio to Google for transcription after you give permission.
          Successful analysis removes the uploaded source file. Failed jobs can retain the file for
          retry. Pasted transcripts are processed on the server. Generating an AI draft sends the
          report title, extracted phrases and selected passages to the local Ollama model.
        </p>
      </section>
      <section>
        <h2>YouTube research</h2>
        <p>
          Choosing a lookup sends the keyword and region to YouTube through YouTube API Services.
          Results are reused for up to six hours. Your competition rating and notes are saved in
          your private report.
        </p>
      </section>
      <section>
        <h2>Your channel connection</h2>
        <p>
          Connecting through Google allows this app to read channel analytics and manage videos. The
          server encrypts authorization tokens. Disconnect removes the stored connection, upload
          sessions and channel activity. It also asks Google to revoke access. If the app cannot
          confirm revocation, use Google Account permissions below. Manage published videos in
          YouTube Studio.
        </p>
        <p>
          You can also revoke access in your Google Account. Use of YouTube features is subject to
          YouTube’s Terms of Service and Google’s Privacy Policy.
        </p>
        <div className="form-actions">
          <Link className="text-link" to="/channel">
            Channel settings
          </Link>
          <a className="text-link" href="https://myaccount.google.com/permissions">
            Google Account permissions
          </a>
          <a className="text-link" href="https://www.youtube.com/t/terms">
            YouTube Terms of Service
          </a>
          <a className="text-link" href="https://policies.google.com/privacy">
            Google Privacy Policy
          </a>
        </div>
      </section>
    </article>
  );
}
