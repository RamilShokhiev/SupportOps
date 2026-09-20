import { BookOpen, Check, ChevronDown, ShieldCheck, TriangleAlert, Users } from 'lucide-react';
import type { ReviewTeam, ReviewTeamSummary, Source } from './types';
import './review-team.css';

const readable = (value:string) => value.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
const providerLabel = (provider:string, role:string) => provider === 'demo' ? (role === 'knowledge' ? 'Hash embeddings + search' : 'Rules simulation') : provider === 'openai' ? 'OpenAI' : provider === 'retailbridge-synthetic-api' ? 'Synthetic API' : 'Server rules';

export function ReviewTeamPanel({team,sources,showSource}:{team:ReviewTeam;sources:Source[];showSource:(source:Source)=>void}) {
  return <section className="panel review-team-panel" aria-label="Review team results">
    <div className="section-header"><h2><Users size={18}/>SupportOps Review Team</h2><span className={`team-outcome ${team.status}`}>{team.status==='passed'?<Check size={14}/>:<TriangleAlert size={14}/>} {team.status==='passed'?'Checks passed':'Needs human review'}</span></div>
    <div className="team-introduction">
      <p>{team.provider==='demo'?'Demo team: rules-based simulation. No live model calls.':'AI reviewers assess the evidence; server rules control routing and permissions.'}</p>
      <div className="team-summary"><span>{team.roles.length} roles</span><span>{team.rounds} review round{team.rounds===1?'':'s'}</span><span>{team.revisions} draft revision{team.revisions===1?'':'s'}</span><span>{team.disagreements.length} concern{team.disagreements.length===1?'':'s'}</span></div>
    </div>
    <div className="team-role-grid">
      {team.roles.map(role=><article className={`team-role ${role.status}`} key={role.role} aria-label={`${readable(role.role)} review`}>
        <div className="team-role-heading"><h3>{role.label}</h3><span className="team-role-status">{role.status==='completed'?<Check size={13}/>:<TriangleAlert size={13}/>} {readable(role.status)}</span></div>
        <span className="team-provider">{providerLabel(role.provider, role.role)}</span>
        <p>{role.summary}</p>
        {role.findings.length>0&&<ul>{role.findings.map((finding,i)=><li key={i}>{finding}</li>)}</ul>}
        {role.source_ids.length>0&&<div className="team-sources">{role.source_ids.map(id=>{const source=sources.find(item=>item.id===id);return source?<button className="text-button" key={id} onClick={()=>showSource(source)}><BookOpen size={12}/>{source.title}</button>:null})}</div>}
        <details className="team-role-details"><summary>Run details<ChevronDown size={12}/></summary><div><span>{(role.elapsed_ms/1000).toFixed(2)}s</span><span>{role.input_tokens+role.output_tokens} tokens</span><span>{role.cost_usd===null?'Cost unavailable':`$${Number(role.cost_usd).toFixed(5)}`}</span></div></details>
      </article>)}
    </div>
    {team.disagreements.length>0&&<div className="team-concerns"><h3>Review concerns</h3><ul>{team.disagreements.map((finding,i)=><li key={`${finding.role}-${finding.code}-${i}`}><TriangleAlert size={15}/><div><strong>{readable(finding.role)} · {finding.blocking?'Blocks recommendation':'For human review'}</strong><p>{finding.message}</p></div></li>)}</ul></div>}
    <div className="team-verdict"><ShieldCheck size={18}/><div><h3>Reviewer verdict</h3><p>{readable(team.reviewer_verdict)}</p>{team.displayed_draft_origin==='local_safety_template'&&<p>The suggested response uses a local clarification template because the generated candidate did not pass review.</p>}<small>Human approval is still required. Up to {team.limits.max_revisions} draft revision{team.limits.max_revisions===1?'':'s'} per run.</small></div></div>
    {team.draft_history.length>0&&<details className="team-draft-history"><summary>Draft review history<ChevronDown size={15}/></summary>{team.draft_history.map((entry,i)=><article key={`${entry.revision}-${i}`}><h3>{entry.revision===0?'Initial draft':`Revision ${entry.revision}`}<span>{readable(entry.verdict)}</span></h3>{entry.findings.length>0&&<ul>{entry.findings.map((finding,index)=><li key={index}>{finding}</li>)}</ul>}<p>{entry.draft}</p></article>)}</details>}
  </section>;
}

export function ReviewTeamOverview({summary}:{summary:ReviewTeamSummary}) {
  return <section className="panel team-overview" aria-label="Review team overview">
    <div className="section-header"><h2><Users size={18}/>Review team outcomes</h2><span className="subtle-pill">{summary.runs} saved runs</span></div>
    <p className="team-overview-note">Saved review activity in this workspace. Counts include repeated runs; they do not measure answer quality.</p>
    <div className="team-overview-metrics"><div><strong>{summary.tickets}</strong><span>Team-reviewed tickets</span></div><div><strong>{summary.blocked}</strong><span>Runs with unresolved concerns</span></div><div><strong>{summary.revised}</strong><span>Runs with a revised draft</span></div><div><strong>{summary.disagreements}</strong><span>Review concerns recorded</span></div></div>
    <div className="team-overview-details"><div><h3>Concerns by role</h3>{Object.entries(summary.by_role).length?<ul>{Object.entries(summary.by_role).map(([role,count])=><li key={role}>{readable(role)} <strong>{count}</strong></li>)}</ul>:<p>No review concerns recorded.</p>}</div><div><h3>Human decisions</h3>{Object.entries(summary.human_decisions).length?<ul>{Object.entries(summary.human_decisions).map(([decision,count])=><li key={decision}>{readable(decision)} <strong>{count}</strong></li>)}</ul>:<p>No human decisions recorded yet.</p>}</div></div>
  </section>;
}
