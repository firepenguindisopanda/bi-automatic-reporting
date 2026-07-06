import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import BIPage from './pages/BIPage'
import MarketResearchPage from './pages/MarketResearchPage'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<BIPage />} />
        <Route path="/market-research" element={<MarketResearchPage />} />
      </Route>
    </Routes>
  )
}
