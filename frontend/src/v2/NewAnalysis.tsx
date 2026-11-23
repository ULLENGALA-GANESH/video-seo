import { useRef, useState, type FormEvent } from 'react';
import { ArrowRight, FileText, Film, UploadCloud, X } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { errorText, request, type Report } from './api';

const formats = '.mp4,.mov,.webm,.mkv,.m4v,.mp3,.wav,.m4a,.ogg';

export default function NewAnalysis() {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const [source, setSource] = useState<'transcript' | 'video'>('transcript');
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [transcript, setTranscript] = useState('');
  const [consent, setConsent] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  function selectFile(candidate?: File) {
    if (!candidate) return;
    if (
      candidate.size > 100 * 1024 * 1024 ||
      candidate.size === 0 ||
      !formats.split(',').some((ext) => candidate.name.toLowerCase().endsWith(ext))
    ) {
      setError('Choose a supported media file between 1 byte and 100 MiB.');
      return;
    }
    setFile(candidate);
    setError('');
    if (!title.trim()) setTitle(candidate.name.replace(/\.[^.]+$/, '').slice(0, 140));
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    if (!title.trim() || (source === 'transcript' ? !transcript.trim() : !file || !consent)) {
      setError('Add a title and complete the selected source.');
      return;
    }
    setError('');
    setPending(true);
    const body = new FormData();
    body.set('title', title.trim());
    if (source === 'transcript') body.set('transcript', transcript.trim());
    else if (file) {
      body.set('file', file);
      body.set('consent', 'true');
    }
    try {
      const report = await request<Report>('/api/analyses', { method: 'POST', body });
      navigate('/analyses/' + encodeURIComponent(report.id), { replace: true });
    } catch (reason) {
      setError(errorText(reason));
      setPending(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Workspace</p>
          <h1>
            New analysis<span className="heading-dot">.</span>
          </h1>
          <p className="subtle">
            Start with a transcript or upload media to find phrases grounded in your content.
          </p>
        </div>
      </div>
      <form className="surface workspace-form form-stack" onSubmit={submit}>
        <label className="field">
          <span>Report title</span>
          <input
            type="text"
            placeholder="Give your report a name"
            required
            maxLength={140}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <fieldset className="source-field">
          <legend>Source</legend>
          <div className="tabs" role="group" aria-label="Analysis source">
            <button
              type="button"
              className={'tab' + (source === 'transcript' ? ' active' : '')}
              aria-pressed={source === 'transcript'}
              onClick={() => {
                setSource('transcript');
                setError('');
              }}
            >
              <FileText size={16} />
              Paste transcript
            </button>
            <button
              type="button"
              className={'tab' + (source === 'video' ? ' active' : '')}
              aria-pressed={source === 'video'}
              onClick={() => {
                setSource('video');
                setError('');
              }}
            >
              <Film size={16} />
              Upload media
            </button>
          </div>
        </fieldset>
        {source === 'transcript' ? (
          <label className="field">
            <span>Transcript</span>
            <textarea
              rows={10}
              required
              maxLength={60000}
              value={transcript}
              placeholder="Paste the spoken text from your video…"
              onChange={(event) => setTranscript(event.target.value)}
            />
            <small>{transcript.length.toLocaleString()} / 60,000 characters</small>
          </label>
        ) : (
          <>
            <div
              className={'dropzone' + (dragging ? ' dragging' : '')}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                selectFile(event.dataTransfer.files[0]);
              }}
            >
              <UploadCloud size={25} />
              <strong>{file ? file.name : 'Choose or drop a media file'}</strong>
              <small>
                {file
                  ? (file.size / 1048576).toFixed(1) + ' MiB'
                  : 'MP4, MOV, WebM, MKV, M4V, MP3, WAV, M4A, OGG · 100 MiB · 15 min max'}
              </small>
              <input
                ref={fileInput}
                type="file"
                accept={formats}
                hidden
                aria-label="Media file"
                onChange={(event) => {
                  selectFile(event.target.files?.[0]);
                  event.target.value = '';
                }}
              />
              <div className="form-actions">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => fileInput.current?.click()}
                >
                  {file ? 'Replace file' : 'Choose file'}
                </button>
                {file && (
                  <button type="button" className="btn btn-ghost" onClick={() => setFile(null)}>
                    <X size={15} />
                    Remove
                  </button>
                )}
              </div>
            </div>
            <label className="check-field">
              <input
                type="checkbox"
                checked={consent}
                required
                onChange={(event) => setConsent(event.target.checked)}
              />
              <span>
                I agree to send this audio to Google's speech recognition service for English
                transcription.
              </span>
            </label>
          </>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="form-actions">
          <button disabled={pending} className="btn btn-primary">
            {pending ? 'Starting analysis…' : 'Start analysis'}
            <ArrowRight size={16} />
          </button>
          <Link to="/" className="btn btn-ghost">
            Cancel
          </Link>
        </div>
      </form>
    </>
  );
}
