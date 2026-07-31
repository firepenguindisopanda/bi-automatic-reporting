import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import BIPage from './pages/BIPage'
import JobPage from './pages/JobPage'
import MarketResearchPage from './pages/MarketResearchPage'
import ResearchResultPage from './pages/ResearchResultPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<BIPage />} />
        <Route path="/research" element={<MarketResearchPage />} />
        {/* The job id lives in the route so a refresh restores the run. */}
        <Route path="/jobs/:jobId" element={<JobPage />} />
        <Route path="/research/:jobId/result" element={<ResearchResultPage />} />
      </Route>
    </Routes>
  )
}
